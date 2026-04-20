import json
import subprocess

def configure_networking():
    enable_forwarding_on_host()
    assert forwarding_enabled_on_host()
    
    create_firecracker_table()
    create_pr_chain()
    create_filter_chain()

def configure_vm_host_networking(vm):
    vm.ip = get_vm_ip(vm.id) 
    vm.tap, vm.tap_ip = create_tap(vm.id)
    add_rules(vm)

def enable_forwarding_on_host():
    with open("/proc/sys/net/ipv4/ip_forward", "a") as f:
        f.write("1\n")

def forwarding_enabled_on_host():
    with open("/proc/sys/net/ipv4/ip_forward") as f:
        return f.read().strip() == "1"

def create_firecracker_table():     
    subprocess.run(["nft", "add", "table", "firecracker"], check=True)

def create_pr_chain():
    subprocess.run([
        "nft",
        "add", "chain", "firecracker", "postrouting",
        "{", 
            "type", "nat",
            "hook", "postrouting",
            "priority", "srcnat;",
            "policy", "accept;",
        "}",
    ], check=True)

def create_filter_chain():
    subprocess.run([
        "nft",
        "add", "chain", "firecracker", "filter",
        "{", 
            "type", "filter",
            "hook", "forward",
            "priority", "filter;",
            "policy", "accept;",
        "}",
    ], check=True)

def get_vm_ip(vm_id):
    vm_n = vm_id * 4 + 2
    return f"172.16.{vm_n // 256}.{vm_n % 256}" 

def create_tap(vm_id):
    tap_n = vm_id * 4 + 1
    tap_ip = f"172.16.{tap_n // 256}.{tap_n % 256}" 
    tap_name = f"fc-tap-{vm_id}"

    # delete if already exists, may fail if doesn't exist so no check
    subprocess.run(("ip", "link", "del", tap_name), capture_output=True)
    subprocess.run(("ip", "tuntap", "add", tap_name, "mode", "tap"), 
        capture_output=True, check=True)
    subprocess.run(("ip", "addr", "add", f"{tap_ip}/30", "dev", tap_name),
        capture_output=True, check=True)
    subprocess.run(("ip", "link", "set", tap_name, "up"),
        capture_output=True, check=True)

    return tap_name, tap_ip

def add_rules(vm):
    if_name = get_default_dev()

    subprocess.run(("nft", "add", "rule", "firecracker", 
        "postrouting", "ip", "saddr", vm.ip,
        "oifname", if_name, "counter", "masquerade"),
         capture_output=True,
         check=True)

    subprocess.run(("nft", "add", "rule", "firecracker",
        "filter", "iifname", vm.tap, "oifname", if_name, "accept"),
         capture_output=True,
         check=True)

def get_default_dev():
    ip_r = subprocess.run(("ip", "-j", "route", "list", "default"), capture_output=True, text=True)
    ip_j = json.loads(ip_r.stdout)

    if len(ip_j) == 0:
        return "enlp0"
    else:
        return ip_j[0]["dev"]

