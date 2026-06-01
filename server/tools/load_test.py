import sys
import time
import random
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

def run_request(base_url, endpoint, api_key=None):
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
        
    start_time = time.time()
    try:
        if endpoint == "nearby":
            url = f"{base_url}/api/community/nearby?country=US&limit=10"
            r = requests.get(url, headers=headers, timeout=5.0)
        else: # analyze
            url = f"{base_url}/api/analyze?locale=en"
            # Send a fake minimal 1x1 png image
            img_bytes = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
            files = {"image": ("test.png", img_bytes, "image/png")}
            r = requests.post(url, headers=headers, files=files, timeout=10.0)
            
        latency = time.time() - start_time
        return r.status_code, latency, None
    except Exception as e:
        latency = time.time() - start_time
        return 0, latency, str(e)

def main():
    parser = argparse.ArgumentParser(description="PlantDiseases Server Load Test")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the server")
    parser.add_argument("--concurrency", type=int, default=10, help="Number of concurrent threads")
    parser.add_argument("--requests", type=int, default=100, help="Total number of requests to make")
    parser.add_argument("--api-key", default=None, help="Optional X-API-Key header value")
    args = parser.parse_args()

    print(f"Starting load test targeting: {args.url}")
    print(f"Concurrency: {args.concurrency}, Total Requests: {args.requests}")

    endpoints = ["nearby", "analyze"]
    latencies = []
    status_codes = {}
    errors = []

    start_wall = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {
            executor.submit(run_request, args.url, random.choice(endpoints), args.api_key): i
            for i in range(args.requests)
        }
        
        for future in as_completed(futures):
            status, latency, err = future.result()
            latencies.append(latency)
            status_codes[status] = status_codes.get(status, 0) + 1
            if err:
                errors.append(err)

    end_wall = time.time()
    total_time = end_wall - start_wall

    print("\n--- RESULTS ---")
    print(f"Total Wall-Clock Time: {total_time:.2f} seconds")
    print(f"Throughput: {len(latencies) / total_time:.2f} req/sec")
    print(f"Status codes: {status_codes}")
    if errors:
        print(f"Errors encountered ({len(errors)}): {set(errors)}")
    if latencies:
        print(f"Min Latency: {min(latencies):.4f}s")
        print(f"Max Latency: {max(latencies):.4f}s")
        print(f"Average Latency: {sum(latencies)/len(latencies):.4f}s")

if __name__ == "__main__":
    main()
