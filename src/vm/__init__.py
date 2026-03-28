import subprocess
import shutil
from pathlib import Path
import json
import tempfile
import requests_unixsocket
import time

import os, signal, atexit

def _cleanup_all_vms():
    for fvm in list(_active_vms):
        try:
            fvm.__exit__(None, None, None)
        except:
            pass

def _signal_handler(signum, frame):
    _cleanup_all_vms()
    os._exit(0) # doesn't raise an exception

_active_vms = []
atexit.register(_cleanup_all_vms)
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

subprocess.run(
    ["sudo", "tee", "/proc/sys/net/ipv4/ip_forward"],
    input=bytes("1\n", "ascii"),
    capture_output=True,
)

subprocess.run(["sudo", "nft", "add", "table", "firecracker"])

subprocess.run([
    "sudo", "nft",
    "add", "chain", "firecracker", "postrouting",
    "{", "type", "nat", "hook", "postrouting", "priority", "srcnat;", "policy", "accept;", "}"
])

subprocess.run([
    "sudo", "nft",
    "add", "chain", "firecracker", "filter",
    "{", "type", "filter", "hook", "forward", "priority", "filter;", "policy", "accept;", "}"
])


class IDs:
    def __init__(self):
        self.ids = []
        self.next_id = 0
    
    def acquire(self):
        n = self.next_id    
        self.next_id = n + 1
        self.ids.append(n)
        assert self.ids.count(n) == 1, "Could not assign ID"
        return n

id_tracker = IDs()

class FirecrackerVM:
    def __init__(self, keep_filesystem=False, fqdn="", rootfs_image="/home/td/Documents/Code/td/scripts/firecracker/ubuntu-python.ext4", kernel="/home/td/Documents/Code/td/scripts/firecracker/vmlinux-6.1.155"):
        self.id = id_tracker.acquire()
        self.keep_filesystem = keep_filesystem
        self.workdir = Path(tempfile.mkdtemp(prefix=f"vm-{self.id}-"))
        self.firecracker_out = self.workdir / "firecracker-out.log"
        self.firecracker_log = self.workdir / "firecracker.log"
        self.api_socket = self.workdir/ "api.sock"
  
        self.session = requests_unixsocket.Session()
        self.base_url = f"http+unix://{self.api_socket.as_posix().replace('/', '%2F')}"

        self._copy_kernel(kernel)
        self._copy_image(rootfs_image)
        self._set_mac()
        self._create_tap()

        self.ssh_opts = ("-o", "StrictHostKeyChecking=no", "-i", "/home/td/Documents/Code/td/scripts/firecracker/vm_key")
        self.ssh_pref = ("ssh",) + self.ssh_opts + (f"root@{self.ip}",)

        
        self.log = open(self.firecracker_out, "wb")
        self.proc = subprocess.Popen(
            ("/usr/local/bin/firecracker",
             "--api-sock", str(self.api_socket),
             ), #"--enable-pci"),
            stdout=self.log,
            stderr=self.log,
            stdin=subprocess.DEVNULL,  # Don't inherit stdin
            start_new_session=True,     # Start in new process group
        )

        _active_vms.append(self)
        
        while not self.api_socket.exists():
            time.sleep(0.02)
        
        self._put(
            "/logger",
            {
               "log_path": str(self.firecracker_log),
                "level": "Debug",
                "show_level": True,
                "show_log_origin": True,
            },
        )

        self._put(
            "/boot-source",
            {
                "kernel_image_path": str(self.workdir / "kernel"),
                "boot_args": f"console=ttyS0 reboot=k panic=1 ip={self.ip}::{self.tap_ip}:255.255.255.252::eth0:off", ##
            },
        )       

        self._put(
            "/drives/rootfs",
            {
                "drive_id": "rootfs",
                "path_on_host": str(self.workdir / "rootfs.ext4"),
                "is_root_device": True,
                "is_read_only": False,
            },
        )       

        self._put(
            "/network-interfaces/eth0",
            {
                "iface_id": "eth0",
                "guest_mac": self.mac,
                "host_dev_name": self.tap,
            },
        )       

        self._put(
            "/machine-config",
            {
                "vcpu_count": 1,
                "mem_size_mib": 2048,
            },
        )       

        self._put(
            "/actions",
            {
                "action_type": "InstanceStart",
            },
        )       

        self.remove_old_host_key()
        self._wait_ready()

        if fqdn != "":
            assert " " not in fqdn, "Check FQDN format"
            hostname = fqdn.split(".", 1)[0]
            self.run("hostnamectl set-hostname " + hostname)
            self.run("systemctl restart systemd-hostnamed")
            self.run(f"echo '127.0.1.1 \t{fqdn} {hostname}' > /etc/hosts")
            self.fqdn = fqdn
            self.hostname = hostname

        #time.sleep(2)
        #self.run(f"ip route add default via {self.tap_ip} dev eth0")
    
    def _set_mac(self):
        n = self.id * 4 + 2
        self.mac = f"06:00:AC:10:{n // 256:02x}:{n % 256:02x}"

    def _set_device(self):
        ip_r = subprocess.run(("ip", "-j", "route", "list", "default"), capture_output=True, text=True)
        ip_j = json.loads(ip_r.stdout)
        if len(ip_j) == 0:
            self.device = "enlp0"
        else:
            self.device = ip_j[0]["dev"]

    def _create_tap(self):
        tap_n = self.id * 4 + 1
        vm_n = self.id * 4 + 2

        tap_ip = f"172.16.{tap_n // 256}.{tap_n % 256}" 
        vm_ip = f"172.16.{vm_n // 256}.{vm_n % 256}" 
        self.tap_ip = tap_ip
        self.ip = vm_ip


        self.tap = f"tap-vm{self.id}"
        subprocess.run(("sudo", "ip", "link", "del", self.tap))
        subprocess.run(("sudo", "ip", "tuntap", "add", self.tap, "mode", "tap"))
        subprocess.run(("sudo", "ip", "addr", "add", f"{tap_ip}/30", "dev", self.tap))
        subprocess.run(("sudo", "ip", "link", "set", self.tap, "up"))
        self._set_device()
        subprocess.run(("sudo", "nft", "add", "rule", "firecracker", "postrouting", "ip", "saddr", vm_ip, "oifname", self.device, "counter", "masquerade"))
        subprocess.run(("sudo", "nft", "add", "rule", "firecracker", "filter", "iifname", self.tap, "oifname", self.device, "accept"))

    def _copy_kernel(self, kernel_path):
        shutil.copy2(src=kernel_path, dst= self.workdir / "kernel")

    def _copy_image(self, image_path):
        shutil.copy2(src=image_path, dst= self.workdir / "rootfs.ext4")

    def remove_old_host_key(self):
        kh = Path("~/.ssh/known_hosts").expanduser()
        rm = ("ssh-keygen", "-f", str(kh), "-R", self.ip)
        r = subprocess.run(rm, capture_output=True)

    def _wait_ready(self, timeout=10):
        start = time.time()

        while time.time() < start + timeout:
            r = subprocess.run(self.ssh_pref + ("echo connected",), capture_output=True, text=True)
            if r.stdout.strip() == "connected":
               return
            time.sleep(0.1)

        raise RuntimeError("Could not connect to VM")

    def run(self, cmd):
        subprocess.run(self.ssh_pref + (cmd,))

    def scp(self, from_, to):
        dest = f"root@{self.ip}:{to}"
        cmd = ("scp",) + self.ssh_opts + (from_, dest,)
        r = subprocess.run(cmd, capture_output=True)
    
    def _put(self, path, payload):
        url = self.base_url + path
        r = self.session.put(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
    
    def __repr__(self):
        return f"FirecrackerVM(id={self.id}, workdir={repr(self.workdir)})"
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        #print(f"Exiting VM {self.id}!")
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except:
            self.proc.kill()
        
        self.log.close()
        
        if not self.keep_filesystem:
            shutil.rmtree(self.workdir)
        
        # Remove from active list
        try:
            _active_vms.remove(self)
        except ValueError:
            pass

