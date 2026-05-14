import vm

with vm.FirecrackerVM() as fvm:
    raise RuntimeError("The vm should still be cleaned up")

