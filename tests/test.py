import vm

import json
import time

def print_results(result_tuple):
    description, passed, stdout, stderr, performance = result_tuple
    if passed:
        print("PASSED:", description)
        print("----- stdout -----")
        print(stdout)
        print("----- stderr -----")
        print(stderr)
        print("----- performance -----")
        print(json.dumps(performance, indent=4))
    
def ping_google():
    start = time.time()

    with vm.FirecrackerVM() as fvm:
        created_in = time.time() - start
        result = fvm.run("ping -q -w 2 -c 1 google.com")
        return "Ping google", result.returncode == 0, result.stdout, result.stderr, {"created_in": created_in}

if __name__ == "__main__":
    print_results(ping_google())
