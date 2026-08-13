import json
import subprocess
from dataclasses import dataclass

from vm.utils import run

@dataclass
class NFTRule:
    table: str
    chain: str
    handle: int

@dataclass
class VMNetwork:
    vm_ip: str
    tap_ip: str
    tap: str
    dev: str
    rules: tuple[NFTRule]

def create_vm_network(vm):
    vm_ip = get_vm_ip(vm.id)
    tap_ip = get_tap_ip(vm.id)
    tap = f"fc-tap-{vm.id}"
    dev = get_default_dev()

    create_tap(tap, tap_ip)

    rules = add_rules(vm_ip, tap, dev)

    return VMNetwork(
        vm_ip = vm_ip,
        tap_ip = tap_ip,
        tap = tap,
        dev = dev,
        rules = rules
    )

def remove_vm_network(vm_network):
    run(("ip", "link", "del", vm_network.tap))
    for rule in vm_network.rules:
        delete_rule(rule)
    
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

def delete_tap(tap_name):
    run(("ip", "link", "del", tap_name))

def add_rules(vm_ip, tap, device):
    return (add_pr_rule(vm_ip, device), add_filter_rule(device, tap))

def nft_add_rule(args):
    r = run(("nft", "-eaj", "add", "rule", "firecracker") + args)
    j = json.loads(r.stdout)["nftables"][0]["add"]["rule"]
    return NFTRule(j["table"], j["chain"], j["handle"])

def add_pr_rule(vm_ip, device):
    return nft_add_rule(("postrouting", "ip", "saddr", vm_ip,
        "oifname", device, "counter", "masquerade"))

def add_filter_rule(device, tap):
    return nft_add_rule(("filter", "iifname", tap, "oifname", device, "accept"))

def delete_rule(rule):
    run(("nft", "delete", "rule", rule.table, rule.chain, "handle", str(rule.handle)))

def get_default_dev():
    ip_r = run(("ip", "-j", "route", "list", "default"))
    ip_j = json.loads(ip_r.stdout)

    if len(ip_j) == 0:
        return "enlp0"
    else:
        return ip_j[0]["dev"]

