import json
import subprocess
from dataclasses import dataclass

from vm.utils import run

@dataclass
class VMNetwork:
    vm_ip: str
    tap_ip: str
    tap: str
    dev: str

def create_vm_network(vm):
    return VMNetwork(
        vm_ip = get_vm_ip(vm.id),
        tap_ip = get_tap_ip(vm.id),
        tap = f"fc-tap-{vm.id}",
        dev = get_default_dev()
    )

def apply_vm_network(vm_network):
    create_tap(vm_network.tap, vm_network.tap_ip)
    add_rules(vm_network.vm_ip, vm_network.tap, vm_network.dev)

    
def configure_networking():
    enable_forwarding_on_host()
    assert forwarding_enabled_on_host()
    
    create_firecracker_table()
    create_pr_chain()
    create_filter_chain()

def enable_forwarding_on_host():
    # This lets us run with lower permissions if ip forwarding
    # has already been enabled
    if forwarding_enabled_on_host(): return

    with open("/proc/sys/net/ipv4/ip_forward", "a") as f:
        f.write("1\n")

def forwarding_enabled_on_host():
    with open("/proc/sys/net/ipv4/ip_forward") as f:
        return f.read().strip() == "1"

def create_firecracker_table():     
    run(("nft", "add", "table", "firecracker"))

def create_pr_chain():
    run((
        "nft",
        "add", "chain", "firecracker", "postrouting",
        "{", 
            "type", "nat",
            "hook", "postrouting",
            "priority", "srcnat;",
            "policy", "accept;",
        "}",
    ))

def create_filter_chain():
    run((
        "nft",
        "add", "chain", "firecracker", "filter",
        "{", 
            "type", "filter",
            "hook", "forward",
            "priority", "filter;",
            "policy", "accept;",
        "}",
    ))

def get_vm_ip(vm_id):
    vm_n = vm_id * 4 + 2
    return f"172.16.{vm_n // 256}.{vm_n % 256}" 

def get_tap_ip(vm_id):
    tap_n = vm_id * 4 + 1
    return f"172.16.{tap_n // 256}.{tap_n % 256}" 

def create_tap(tap_name, tap_ip):
    # delete if already exists, may fail if doesn't exist so no check
    subprocess.run(("ip", "link", "del", tap_name), capture_output=True)
    run(("ip", "tuntap", "add", tap_name, "mode", "tap"))
    run(("ip", "addr", "add", f"{tap_ip}/30", "dev", tap_name))
    run(("ip", "link", "set", tap_name, "up"))
    return

def add_rules(vm_ip, tap, device):
    run(("nft", "add", "rule", "firecracker", 
        "postrouting", "ip", "saddr", vm_ip,
        "oifname", device, "counter", "masquerade"))

    run(("nft", "add", "rule", "firecracker",
        "filter", "iifname", tap, "oifname", device, "accept"))

def get_default_dev():
    ip_r = run(("ip", "-j", "route", "list", "default"))
    ip_j = json.loads(ip_r.stdout)

    if len(ip_j) == 0:
        return "enlp0"
    else:
        return ip_j[0]["dev"]

