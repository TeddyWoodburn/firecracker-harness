import vm

import json
import time

from threading import Thread
import subprocess

def print_results(result_tuple):
    description, passed, details, performance = result_tuple

    if passed:
        print("PASSED: ", end="")
    else:
        print("FAILED: ", end="")

    print(description)

    for k, v in details.items():
        print(f"----- {k} -----")
        print(v)

    print("----- performance -----")
    print(json.dumps(performance, indent=4))
    

def ping_google():
    start = time.time()

    with vm.FirecrackerVM() as fvm:
        created_in = time.time() - start
        result = fvm.run("ping -q -w 2 -c 1 google.com")
        return "Ping google", result.returncode == 0, {"stdout": result.stdout, "stderr": result.stderr}, {"created_in": created_in}

def uname():
    start = time.time()

    with vm.FirecrackerVM() as fvm:
        created_in = time.time() - start
        result = fvm.run("uname -a")
        return "uname -a", result.returncode == 0, {"stdout": result.stdout, "stderr": result.stderr}, {"created_in": created_in, "run_in": time.time() - start}



def ping(from_, to):
    results = []
    result = subprocess.run(("ping", "-q", "-w", "1", "-c", "1", from_.ip), capture_output=True, text=True)
    results.append(("host", from_.ip, result.returncode == 0))
    
    for target in to:
        result = from_.run(f"ping -w 5 -c 1 -v {target.ip}")
        results.append((from_.ip, target.ip, result.returncode == 0))
    
    return all(r[2] for r in results), results 

def ping_when_all_ready(vm_list, results_dict, n, timeout=20):
    start = time.time()

    with vm.FirecrackerVM() as fvm:
        vm_list.append(fvm)

        while time.time() < start + timeout:
            if len(vm_list) == n:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Timed out waiting for the other vms to come up")
        
        passed, results = ping(fvm, vm_list)
        results_dict[fvm.ip] = passed, results

        while time.time() < start + timeout:
            if len(results_dict) == n:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Timed out waiting for other threads to finish")
            
def ping_vms():
    start = time.time()
    vm_list = []
    results_dict = {}

    n_vms = 3
    threads = [Thread(target=ping_when_all_ready, args=(vm_list, results_dict, n_vms)) for _ in range(n_vms)]

    for t in threads:
        t.start()

    for t in threads:
        t.join()
    
    details = {}

    for k, v in results_dict.items():
        info = ""
        for origin, destination, success in v[1]:
            if success:
                info += f"{origin} CAN ping {destination}\n"
            else:
                info += f"{origin} CANNOT ping {destination}\n"
        details[k] = info

    return "ping between vms", all(v[0] for v in results_dict.values()), details, {"completed_in": time.time() - start}


if __name__ == "__main__":
    print_results(uname())
    #print_results(ping_google())
    print_results(ping_vms())
