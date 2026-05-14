import shutil

def shutdown(vm):
    vm.run("shutdown now")

def files(vm.files):
    shutil.rmtree(vm.files.workdir)

def nw(vm):
    ""
    
