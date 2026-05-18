import shutil

def shutdown(vm):
    vm.run("shutdown now")
    try:
        vm.proc.terminate()
        vm.proc.wait(timeout=5)
    except:
        vm.proc.kill()

def fds(fd):
    for f in fd.values():
        f.close()

def files(files):
    shutil.rmtree(files.workdir)

def nw(vm):
    ""
    
