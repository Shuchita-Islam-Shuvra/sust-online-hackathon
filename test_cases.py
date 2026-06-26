import os
import sys
import json
import httpx

# Default server URL
SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:8000").rstrip("/")

def print_result_row(ticket_id, field, expected, actual, passed):
    status_str = "\033[92mPASS\033[0m" if passed else "\033[91mFAIL\033[0m"
    print(f"| {ticket_id:<12} | {field:<24} | {str(expected):<30} | {str(actual):<30} | {status_str} |")

def main():
    # Path to test cases JSON
    cases_file = "test_cases.json"
    if len(sys.argv) > 1:
        cases_file = sys.argv[1]
        
    if not os.path.exists(cases_file):
        print(f"Error: Case file '{cases_file}' not found.")
        sys.exit(1)
        
    with open(cases_file, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            test_cases = data.get("cases", [])
        except Exception as e:
            print(f"Error parsing JSON from {cases_file}: {e}")
            sys.exit(1)
            
    if not test_cases:
        print("Error: No test cases found in 'cases' key.")
        sys.exit(1)
        
    print(f"Found {len(test_cases)} test cases in {cases_file}.")
    print(f"Connecting to server at {SERVER_URL}...")
    
    # Check health
    try:
        r_health = httpx.get(f"{SERVER_URL}/health")
        if r_health.status_code == 200:
            print("Server /health check: OK")
        else:
            print(f"Server health check returned status {r_health.status_code}")
    except Exception as e:
        print(f"Could not connect to server: {e}")
        print("Please start the FastAPI server before running this test script.")
        sys.exit(1)

    print("\nStarting test executions...\n")
    print("-" * 110)
    print(f"| {'Ticket ID':<12} | {'Field Evaluated':<24} | {'Expected Value':<30} | {'Actual Value':<30} | {'Status':<4} |")
    print("-" * 110)
    
    total_checks = 0
    passed_checks = 0
    failed_tickets = set()
    
    for case in test_cases:
        ticket_id = case.get("id", "unknown")
        payload = case.get("input", {})
        expected = case.get("expected_output", {})
        
        try:
            response = httpx.post(f"{SERVER_URL}/analyze-ticket", json=payload, timeout=35.0)
        except Exception as e:
            print(f"| {ticket_id:<12} | Request Failed           | N/A                            | {str(e)[:30]:<30} | \033[91mFAIL\033[0m |")
            failed_tickets.add(ticket_id)
            continue
            
        if response.status_code != 200:
            print(f"| {ticket_id:<12} | HTTP status {response.status_code:<12} | N/A                            | {response.text[:30]:<30} | \033[91mFAIL\033[0m |")
            failed_tickets.add(ticket_id)
            continue
            
        res_data = response.json()
        
        # Fields to compare
        fields_to_compare = [
            "case_type",
            "department",
            "evidence_verdict",
            "relevant_transaction_id",
            "severity",
            "human_review_required"
        ]
        
        for field in fields_to_compare:
            if field in expected:
                exp_val = expected[field]
                act_val = res_data.get(field)
                
                # Check match (handle none/null strings vs None type gracefully)
                is_match = False
                if exp_val is None or exp_val == "null":
                    is_match = (act_val is None or act_val == "null" or act_val == "")
                else:
                    is_match = (str(exp_val).lower() == str(act_val).lower())
                    
                total_checks += 1
                if is_match:
                    passed_checks += 1
                else:
                    # Note: We will show all fields, but we want to log if it failed.
                    failed_tickets.add(ticket_id)
                    
                print_result_row(ticket_id, field, exp_val, act_val, is_match)
        
        # Print divider between tickets
        print("-" * 110)
        
    print(f"\nTest Summary: Passed {passed_checks}/{total_checks} assertions.")
    if failed_tickets:
        print(f"\033[91mFailed Tickets:\033[0m {', '.join(failed_tickets)}")
        sys.exit(1)
    else:
        print("\033[92mAll tests passed successfully!\033[0m")
        sys.exit(0)

if __name__ == "__main__":
    main()
