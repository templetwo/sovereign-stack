#!/usr/bin/env python3
"""Antigravity connector for the Sovereign Stack.

A thin stdio MCP client: spawns the local `sovereign` server, performs the
MCP initialize handshake, and exposes `tools/list` + `tools/call` over a small
CLI. Built so an external editor (Google Antigravity / Gemini) can reach the
stack through the stdio transport without holding any of the stack's internals.

Originally authored in the Antigravity scratch workspace; grafted into the repo
here so the connector lives with the other cross-substrate clients
(grok_bridge, openai_bridge) and never depends on a scratch checkout.

Resolution order for the `sovereign` binary:
  1. --path argument
  2. $SOVEREIGN_BIN
  3. `sovereign` on $PATH
  4. ./venv/bin/sovereign relative to the repo root

Data root resolution:
  1. --root argument
  2. $SOVEREIGN_ROOT
  3. ~/.sovereign
"""
import sys
import os
import json
import shutil
import subprocess
import argparse

# Make sibling client packages importable (bridge_core, and this package's
# bridge_setup) whether run from a checkout or installed.
_CLIENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _CLIENTS_DIR not in sys.path:
    sys.path.insert(0, _CLIENTS_DIR)

try:
    from bridge_setup import SUBSTRATE, governed_call, governed_tool_list, register
except ImportError:  # installed as a package
    from antigravity_connector.bridge_setup import (  # type: ignore
        SUBSTRATE,
        governed_call,
        governed_tool_list,
        register,
    )


def _default_sovereign_path():
    env = os.environ.get("SOVEREIGN_BIN")
    if env:
        return env
    on_path = shutil.which("sovereign")
    if on_path:
        return on_path
    # repo root is two levels up from clients/antigravity_connector/
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(repo_root, "venv", "bin", "sovereign")


def _default_sovereign_root():
    return os.environ.get("SOVEREIGN_ROOT") or os.path.expanduser("~/.sovereign")


def _default_source_instance():
    # Allow attribution to come from the MCP server env block. Supports the
    # conventional SOVEREIGN_SOURCE_INSTANCE and the hyphenated "source-instance"
    # key (as written in Antigravity's mcp_config env). CLI --source-instance
    # still overrides both.
    return (
        os.environ.get("SOVEREIGN_SOURCE_INSTANCE")
        or os.environ.get("source-instance")
        or "antigravity-connector"
    )


class SovereignConnector:
    def __init__(self, sovereign_path=None, sovereign_root=None):
        self.sovereign_path = sovereign_path or _default_sovereign_path()
        self.sovereign_root = sovereign_root or _default_sovereign_root()
        self.process = None

    def start(self, perform_handshake=True):
        env = os.environ.copy()
        env["SOVEREIGN_ROOT"] = self.sovereign_root

        # Start sovereign as a subprocess using stdio transport
        self.process = subprocess.Popen(
            [self.sovereign_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            env=env
        )

        # Perform MCP initialization handshake if requested
        if perform_handshake:
            self._initialize()

    def _send_msg(self, msg):
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("Connector not started; call start() first.")
        msg_str = json.dumps(msg) + "\n"
        self.process.stdin.write(msg_str)
        self.process.stdin.flush()

    def _recv_msg(self):
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("Connector not started; call start() first.")
        line = self.process.stdout.readline()
        if not line:
            # Check if process terminated and print stderr
            if self.process.poll() is not None:
                stderr_content = self.process.stderr.read() if self.process.stderr else ""
                raise RuntimeError(f"Sovereign process terminated with exit code {self.process.returncode}. Stderr: {stderr_content}")
            raise RuntimeError("EOF reached while reading from Sovereign stdout.")
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to decode JSON response: {line}. Error: {e}")

    def _initialize(self):
        # 1. Send 'initialize'
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "antigravity-connector",
                    "version": "1.0"
                }
            }
        }
        self._send_msg(init_req)

        # 2. Receive 'initialize' response
        resp = self._recv_msg()
        if resp.get("id") != 1 or "error" in resp:
            raise RuntimeError(f"Initialization failed: {resp}")

        # 3. Send 'notifications/initialized'
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        self._send_msg(initialized_notification)

    def list_tools(self):
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        self._send_msg(req)
        resp = self._recv_msg()
        if "error" in resp:
            raise RuntimeError(f"Failed to list tools: {resp['error']}")
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, tool_name, arguments=None):
        if arguments is None:
            arguments = {}
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        self._send_msg(req)
        resp = self._recv_msg()
        if "error" in resp:
            raise RuntimeError(f"Tool call failed: {resp['error']}")
        return resp.get("result", {})

    def run_proxy(self, source_instance, substrate):
        import threading
        self.start(perform_handshake=False)

        stdout_lock = threading.Lock()
        pending_tools_list_requests = set()

        class Ring1ForwardException(Exception):
            pass

        def dummy_dispatch(name, args):
            raise Ring1ForwardException()

        def raw_to_parent():
            try:
                if self.process and self.process.stdout:
                    for line in self.process.stdout:
                        if not line:
                            break
                        try:
                            msg = json.loads(line)
                            msg_id = msg.get("id")
                            if msg_id is not None and msg_id in pending_tools_list_requests:
                                pending_tools_list_requests.remove(msg_id)
                                if "error" not in msg:
                                    raw_tools = msg.get("result", {}).get("tools", [])
                                    governed = governed_tool_list(raw_tools, substrate=substrate)
                                    msg["result"] = {"tools": governed}
                                    line = json.dumps(msg) + "\n"
                        except Exception:
                            pass
                        
                        with stdout_lock:
                            sys.stdout.write(line)
                            sys.stdout.flush()
            except Exception as e:
                print(f"Error in proxy raw_to_parent: {e}", file=sys.stderr)

        def raw_err_to_parent():
            try:
                if self.process and self.process.stderr:
                    for line in self.process.stderr:
                        if not line:
                            break
                        sys.stderr.write(line)
                        sys.stderr.flush()
            except Exception:
                pass

        t_out = threading.Thread(target=raw_to_parent, daemon=True)
        t_err = threading.Thread(target=raw_err_to_parent, daemon=True)
        t_out.start()
        t_err.start()

        try:
            for line in sys.stdin:
                if not line:
                    break
                try:
                    msg = json.loads(line)
                    method = msg.get("method")
                    msg_id = msg.get("id")

                    if msg.get("jsonrpc") == "2.0":
                        if msg_id is not None:
                            if method == "initialize":
                                params = msg.setdefault("params", {})
                                client_info = params.setdefault("clientInfo", {})
                                client_info["name"] = "gemini-antigravity"
                                line = json.dumps(msg) + "\n"
                            
                            elif method == "tools/list":
                                pending_tools_list_requests.add(msg_id)
                            
                            elif method == "tools/call":
                                params = msg.get("params", {})
                                name = params.get("name")
                                arguments = params.get("arguments", {})

                                try:
                                    result = governed_call(
                                        dummy_dispatch, name, arguments, source_instance,
                                        substrate=substrate
                                    )
                                    resp = {
                                        "jsonrpc": "2.0",
                                        "id": msg_id,
                                        "result": result
                                    }
                                    with stdout_lock:
                                        sys.stdout.write(json.dumps(resp) + "\n")
                                        sys.stdout.flush()
                                    continue
                                except Ring1ForwardException:
                                    pass

                except Exception as e:
                    print(f"Error handling parent message: {e}", file=sys.stderr)

                if self.process and self.process.stdin:
                    self.process.stdin.write(line)
                    self.process.stdin.flush()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"Error in proxy parent_to_raw: {e}", file=sys.stderr)
        finally:
            self.close()

    def close(self):
        if self.process:
            self.process.terminate()
            self.process.wait()
            self.process = None


def main():
    parser = argparse.ArgumentParser(description="Antigravity Connector for Sovereign Stack")
    parser.add_argument("--list", action="store_true", help="List all available tools")
    parser.add_argument("--call", type=str, help="Call a specific tool by name")
    parser.add_argument("--args", type=str, default="{}", help="JSON string representing the arguments for the tool call")
    parser.add_argument("--path", type=str, default=None, help="Path to sovereign executable (default: $SOVEREIGN_BIN, PATH, or ./venv/bin/sovereign)")
    parser.add_argument("--root", type=str, default=None, help="Sovereign root data directory (default: $SOVEREIGN_ROOT or ~/.sovereign)")
    parser.add_argument("--substrate", type=str, default=SUBSTRATE,
                        help=f"Declared substrate (default: {SUBSTRATE}). Claude-family substrates "
                             f"are full-trust and bypass ring governance; all others are ringed.")
    parser.add_argument("--source-instance", type=str, default=_default_source_instance(),
                        help="Attribution string for Ring 2 write proposals. Defaults to "
                             "$SOVEREIGN_SOURCE_INSTANCE / env 'source-instance' / 'antigravity-connector'.")
    parser.add_argument("--server", action="store_true",
                        help="Run in stdio MCP proxy server mode (default if neither --list nor --call is passed)")

    args = parser.parse_args()

    connector = SovereignConnector(sovereign_path=args.path, sovereign_root=args.root)

    if not os.path.exists(connector.sovereign_path):
        print(f"Error: Sovereign executable not found at {connector.sovereign_path}. "
              f"Set $SOVEREIGN_BIN, put `sovereign` on PATH, or pass --path.", file=sys.stderr)
        sys.exit(1)

    register()  # register the antigravity substrate + context with bridge_core

    try:
        if args.list:
            connector.start(perform_handshake=True)
            tools = governed_tool_list(connector.list_tools(), substrate=args.substrate)
            print(json.dumps(tools, indent=2))
        elif args.call:
            connector.start(perform_handshake=True)
            try:
                tool_args = json.loads(args.args)
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON arguments: {e}", file=sys.stderr)
                sys.exit(1)
            result = governed_call(
                connector.call_tool, args.call, tool_args, args.source_instance,
                substrate=args.substrate,
            )
            print(json.dumps(result, indent=2))
        else:
            connector.run_proxy(args.source_instance, args.substrate)
    except Exception as e:
        print(f"Exception occurred: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
