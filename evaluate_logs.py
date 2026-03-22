import json

def check_run(run_id, calls_file):
    print(f"\nEvaluating run {run_id}")
    with open(calls_file) as f:
        calls = f.readlines()
        
    for line in calls:
        if line.startswith("ERR "):
            print(f"  {line.strip()}")

check_run("f595bc471eb6433eb6b103d73ac88ff1", "f595_calls.txt")
check_run("cbcf4e5df65341348314dc4978729e94", "cbcf_calls.txt")
check_run("ed3529ba7d984453955893449d073f04", "ed352_calls.txt")

