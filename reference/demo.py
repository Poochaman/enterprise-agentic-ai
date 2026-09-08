import argparse
import json
from .workflow import Engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="New or existing directory for durable demo state")
    args = parser.parse_args()
    engine = Engine(args.data)
    try:
        result = engine.submit("demo-acme", {"requestId":"demo-sales", "message":"Please quote a CRM integration"})
        if result["state"] == "awaiting_approval":
            engine.decide("demo-acme-approver", "demo-sales", "approve", action_digest=result["authority"]["actionDigest"])
        if result["state"] != "completed":
            try:
                engine.execute("demo-acme", "demo-sales", fault="after_write")
            except TimeoutError as error:
                print(str(error))
        engine.close()
        engine = Engine(args.data)
        print(json.dumps(engine.execute("demo-acme", "demo-sales"), indent=2))
    finally:
        engine.close()

if __name__ == "__main__":
    main()
