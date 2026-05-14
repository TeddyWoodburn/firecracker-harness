import subprocess
import shutil
from pathlib import Path
import json
import tempfile
import requests_unixsocket
import time

from dataclasses import dataclass

import os, signal, atexit

from vm.networking import configure_networking, configure_vm_host_networking
from vm.ids import tracker
from vm.cleanup import shutdown, files, fds

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

configure_networking()

@dataclass
class Files:
    workdir: Path # the directory on the host to use for the log, stdout/stderr, api_sock, and write-enabled filesystem (may be a copy of the provided rootfs)
    rootfs: Path # the path of the rootfs, e.g Path("/var/lib/firecracker/debian.ext4")
    kernel: Path # the location of the kernel image to use
    api_sock: Path # the unix socket we will use to communicate with Firecracker
    logs: Path # Firecracker logger configured to use this path
    stdout_stderr: Path # the captured stdout and stderr of the Firecracker process
    

class FirecrackerVM:
    def __init__(self, keep_filesystem=False, fqdn="", rootfs_image="/home/td/Documents/Code/td/scripts/firecracker/ubuntu-python.ext4", kernel="/home/td/Documents/Code/td/scripts/firecracker/vmlinux-6.1.155"):
        self.id = tracker.acquire()

        self.keep_filesystem = keep_filesystem

        workdir = Path(tempfile.mkdtemp(prefix=f"vm-{self.id}-"))
        api_sock = workdir/ "api.sock"
        log = workdir / "firecracker.log"
        stdout_stderr = workdir / "firecracker-stdout-stderr.txt"
        self.files = Files(workdir, self._copy_image(rootfs_image, workdir), self._copy_kernel(kernel, workdir), api_sock, log, stdout_stderr)

        self.session = requests_unixsocket.Session()
        self.base_url = f"http+unix://{self.files.api_sock.as_posix().replace('/', '%2F')}"

        self._set_mac()
        configure_vm_host_networking(self)

        self.ssh_opts = ("-o", "StrictHostKeyChecking=no", "-i", "/home/td/Documents/Code/td/scripts/firecracker/vm_key")
        self.ssh_pref = ("ssh",) + self.ssh_opts + (f"root@{self.ip}",)

        
        self.fd = {"log": open(self.files.stdout_stderr, "wb")}
        self.proc = subprocess.Popen(
            ("/usr/local/bin/firecracker",
             "--api-sock", str(self.files.api_sock),
             ),
            stdout=self.fd["log"],
            stderr=self.fd["log"],
            stdin=subprocess.DEVNULL,  # Don't inherit stdin
            start_new_session=True,     # Start in new process group
        )

        _active_vms.append(self)
        
        while not self.files.api_sock.exists():
            time.sleep(0.02)
        
        self._put(
            "/logger",
            {
               "log_path": str(self.files.logs),
                "level": "Debug",
                "show_level": True,
                "show_log_origin": True,
            },
        )

        self._put(
            "/boot-source",
            {
                "kernel_image_path": str(self.files.kernel),
                "boot_args": f"console=ttyS0 reboot=k panic=1 ip={self.ip}::{self.tap_ip}:255.255.255.252::eth0:off", ##
            },
        )       

        self._put(
            "/drives/rootfs",
            {
                "drive_id": "rootfs",
                "path_on_host": str(self.files.rootfs),
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

    def _set_mac(self):
        n = self.id * 4 + 2
        self.mac = f"06:00:AC:10:{n // 256:02x}:{n % 256:02x}"

    def _copy_kernel(self, kernel_path, workdir):
        shutil.copy2(src=kernel_path, dst= workdir / "kernel")
        return workdir / "kernel"

    def _copy_image(self, image_path, workdir):
        shutil.copy2(src=image_path, dst= workdir / "rootfs.ext4")
        return workdir / "rootfs.ext4"

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
        return subprocess.run(self.ssh_pref + (cmd,), capture_output=True, text=True)

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
        shutdown(self)
        fds(self.fd)
        
        if not self.keep_filesystem:
            files(self.files)
        
        # Remove from active list
        try:
            _active_vms.remove(self)
        except ValueError:
            pass

