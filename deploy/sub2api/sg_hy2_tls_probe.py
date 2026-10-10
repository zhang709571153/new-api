"""Verify four candidate GOST paths using cloudflared's official CA bundle."""
import argparse
import concurrent.futures
import json
import socket
import ssl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca", required=True)
    args = parser.parse_args()
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=args.ca)
    context.set_alpn_protocols(["h2"])

    def check(port):
        result = {"port": port, "certificate_verified": False}
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=8) as raw:
                with context.wrap_socket(raw, server_hostname="h2.cftunnel.com") as secure:
                    result.update(certificate_verified=True, protocol=secure.version(),
                                  alpn=secure.selected_alpn_protocol())
        except Exception as exc:
            result["error_type"] = type(exc).__name__
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, range(19464, 19468)))
    print(json.dumps(results))
    raise SystemExit(0 if all(item["certificate_verified"] for item in results) else 1)


if __name__ == "__main__":
    main()
