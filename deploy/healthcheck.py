import urllib.request
import sys

def check_health():
    """
    Validates that the Nexora Backend is reachable and the DB store is active.
    Used by Docker / Kubernetes liveness probes.
    """
    try:
        req = urllib.request.Request("http://localhost:5000/api/v2/health")
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                print("Healthy")
                sys.exit(0)
            else:
                print(f"Unhealthy status: {response.status}")
                sys.exit(1)
    except Exception as e:
        print(f"Healthcheck failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    check_health()
