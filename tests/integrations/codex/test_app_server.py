"""Native app-server evidence and structured transport at the provider boundary."""

import pytest
from hearth.integrations.codex.app_server import evidence
from hearth.residents.models import Refused


def native_result():
    return {
        "protocol": "codex-app-server-0.153.4",
        "launched": True,
        "cancelled": False,
        "error": None,
        "exit_code": -15,
        "events": [
            {
                "method": "thread/started",
                "params": {"thread": {"id": "thread-1", "model": "gpt-6-astra"}},
            },
            {
                "method": "turn/started",
                "params": {"threadId": "thread-1", "turn": {"id": "turn-1"}},
            },
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "tokenUsage": {
                        "total": {
                            "totalTokens": 15,
                            "inputTokens": 10,
                            "cachedInputTokens": 0,
                            "cacheWriteInputTokens": 0,
                            "outputTokens": 5,
                            "reasoningOutputTokens": 0,
                        }
                    },
                },
            },
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "tokenUsage": {
                        "total": {
                            "totalTokens": 30,
                            "inputTokens": 20,
                            "cachedInputTokens": 0,
                            "cacheWriteInputTokens": 0,
                            "outputTokens": 10,
                            "reasoningOutputTokens": 0,
                        }
                    },
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-1",
                        "status": "completed",
                        "error": None,
                        "items": [
                            {
                                "type": "agentMessage",
                                "id": "message-1",
                                "text": "Fictional resident created.",
                                "phase": None,
                            }
                        ],
                    },
                },
            },
        ],
    }


def test_native_terminal_uses_final_cumulative_usage_once():
    result = evidence(native_result())
    assert result.status == "succeeded"
    assert result.output == "Fictional resident created."
    # 20 input tokens at $10/M + 10 output tokens at $50/M; prior total is not added.
    assert result.cost == 700


@pytest.mark.parametrize(
    "damage",
    [
        "foreign_thread",
        "foreign_turn",
        "repeated_terminal",
        "late_usage",
        "missing_start",
        "missing_thread",
        "different_model",
    ],
)
def test_foreign_or_contradictory_native_terminal_cannot_settle(damage):
    result = native_result()
    events = result["events"]
    if damage == "foreign_thread":
        events[2]["params"]["threadId"] = "another-thread"
    elif damage == "foreign_turn":
        events[-1]["params"]["turn"]["id"] = "another-turn"
    elif damage == "repeated_terminal":
        events.append(events[-1])
    elif damage == "late_usage":
        events.append(events[2])
    elif damage == "missing_start":
        del events[1]
    elif damage == "missing_thread":
        del events[0]
    else:
        events[0]["params"]["thread"]["model"] = "different-model"
    with pytest.raises(Refused, match="app_server_receipt_invalid"):
        evidence(result)


@pytest.mark.parametrize(
    "field", ["inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens"]
)
def test_missing_native_counter_preserves_unknown_cost(field):
    result = native_result()
    del result["events"][-2]["params"]["tokenUsage"]["total"][field]
    assert evidence(result).cost is None


@pytest.mark.parametrize(
    "damage", ["backwards", "contradictory_total", "negative", "boolean", "overlapping_cached"]
)
def test_invalid_native_counters_cannot_release_reserve(damage):
    result = native_result()
    counts = result["events"][-2]["params"]["tokenUsage"]["total"]
    if damage == "backwards":
        counts.update(totalTokens=8, inputTokens=6, outputTokens=2)
    elif damage == "contradictory_total":
        counts["totalTokens"] = 31
    elif damage == "negative":
        counts["cachedInputTokens"] = -1
    elif damage == "boolean":
        counts["cachedInputTokens"] = True
    else:
        counts["cachedInputTokens"] = 21
    with pytest.raises(Refused, match="app_server_receipt_invalid"):
        evidence(result)


def test_cancellation_after_launch_keeps_unknown_usage_but_prelaunch_is_zero():
    result = native_result()
    result["events"].pop()
    result["cancelled"] = True
    assert evidence(result).status == "cancelled"
    assert evidence(result).cost is None
    result["launched"] = False
    result["events"] = []
    assert evidence(result).status == "cancelled"
    assert evidence(result).cost == 0


def test_large_cumulative_input_cannot_claim_short_context_pricing():
    result = native_result()
    result["events"][-2]["params"]["tokenUsage"]["total"].update(
        inputTokens=300000, totalTokens=300010
    )
    assert evidence(result).cost is None


_FAKE_CLI = r"""
import json, pathlib, sys, tomllib
EVENTS = __EVENTS__
SCENARIO = __SCENARIO__
args = sys.argv[1:]
if args == ['--version']:
 print('codex-cli 0.153.4');sys.exit()
if args[:2] == ['debug','models']:
 print(json.dumps({'models':[{'slug':'gpt-6-astra','tool_mode':'code_mode_only','multi_agent_version':'v2','apply_patch_tool_type':'freeform','experimental_supported_tools':['clock']}]}));sys.exit()
config = {'mcp_servers':{}, 'plugins':{}, 'model_providers':{}, 'chatgpt_base_url':'https://chatgpt.com/backend-api/'}
for i,arg in enumerate(args):
 if arg != '-c':continue
 key,value=args[i+1].split('=',1)
 value=tomllib.loads('v='+value)['v']
 current=config
 for part in key.split('.')[:-1]:current=current.setdefault(part,{})
 current[key.split('.')[-1]]=value

def send(value):print(json.dumps(value),flush=True)
def reply(id,result):send({'id':id,'result':result})
def completed():
 for event in EVENTS[2:]:send(event)
for line in sys.stdin:
 message=json.loads(line)
 method=message.get('method');rid=message.get('id');params=message.get('params',{})
 if method=='initialize':reply(rid,{'userAgent':'codex-cli/0.153.4'})
 elif method=='config/read':
  effective=json.loads(json.dumps(config))
  if SCENARIO=='unsafe_config':effective['permissions']['reader']['network']['enabled']=True
  if SCENARIO=='extra_file_grant':
   effective['permissions']['reader']['filesystem']['/synthetic/private']='read'
  reply(rid,{'config':effective,'layers':[{'name':{'type':'sessionFlags'},'config':config},{'name':{'type':'user'},'config':{}}]})
 elif method=='skills/list':
  disabled={x['path'] for x in config.get('skills',{}).get('config',[]) if x['enabled'] is False}
  skill={'path':'/synthetic/host/SKILL.md',
         'enabled':'/synthetic/host/SKILL.md' not in disabled}
  reply(rid,{'data':[{'cwd':params['cwds'][0],'errors':[],'skills':[skill]}]})
 elif method=='thread/start':
  assert params['model']=='gpt-6-astra' and params['approvalPolicy']=='never'
  catalog=json.loads(pathlib.Path(config['model_catalog_json']).read_text())['models'][0]
  assert catalog['slug']=='gpt-6-astra' and catalog['tool_mode']=='function'
  assert catalog['multi_agent_version'] is None and catalog['apply_patch_tool_type'] is None
  assert config['features']['shell_tool'] is False and config['features']['code_mode_host'] is False
  assert config['permissions']['reader']['filesystem']['/']=='deny'
  result={'thread':EVENTS[0]['params']['thread'],'model':'gpt-6-astra','modelProvider':'openai','approvalPolicy':'never','sandbox':{'type':'readOnly','networkAccess':False},'activePermissionProfile':{'id':'reader','extends':None},'instructionSources':[]}
  if SCENARIO=='instructions':result['instructionSources']=['/synthetic/AGENTS.md']
  reply(rid,result);send(EVENTS[0])
 elif method=='turn/start':
  reply(rid,{'turn':{'id':'turn-1'}});send(EVENTS[1])
  if SCENARIO=='stall':continue
  if SCENARIO=='prose':
   EVENTS[-1]['params']['turn']['items'][0]['text']='{"tool":"hearth_probe","arguments":{}}'
   completed();continue
  if SCENARIO=='user_input':
   send({'id':2,'method':'item/tool/requestUserInput',
         'params':{'threadId':'thread-1','turnId':'turn-1','questions':[{'id':'q1'}]}})
   continue
  if SCENARIO in {'reroute','compact'}:
   method='model/rerouted' if SCENARIO=='reroute' else 'thread/compacted'
   send({'method':method,'params':{'threadId':'thread-1','turnId':'turn-1','toModel':'other-model'}})
  call={'threadId':'thread-1','turnId':'turn-1','callId':'call-1','namespace':None,
        'tool':'hearth_probe','arguments':{'name':'Fictional reader'}}
  if SCENARIO=='foreign_call':call['turnId']='foreign-turn'
  if SCENARIO=='unknown_tool':call['tool']='grant_operator'
  send({'id':0,'method':'item/tool/call','params':call})
 elif method is None and rid==0:
  if SCENARIO=='refused':
   assert message['result']['success'] is False
   assert 'management_revoked' in message['result']['contentItems'][0]['text']
   completed();continue
  assert message['result']['success'] is True
  assert message['result']['contentItems'][0]['text']=='receipt-1'
  if SCENARIO in {'replay','conflict','limit'}:
   if SCENARIO=='conflict':call['arguments']={'name':'Changed'}
   if SCENARIO=='limit':call['callId']='call-2'
   send({'id':1,'method':'item/tool/call','params':call})
  else:completed()
 elif method is None and rid==2:
  assert 'Interactive input is disabled' in message['result']['answers']['q1']['answers'][0]
  completed()
 elif method is None and rid==1:
  assert message['result']['contentItems'][0]['text']=='receipt-1'
  completed()
"""


def fake_cli(tmp_path, scenario="success"):
    import sys

    binary = tmp_path / "native-fixture"
    binary.write_text(
        "#!"
        + sys.executable
        + "\n"
        + _FAKE_CLI.replace("__EVENTS__", repr(native_result()["events"])).replace(
            "__SCENARIO__", repr(scenario)
        )
    )
    binary.chmod(0o700)
    auth = tmp_path / "auth-home"
    auth.mkdir()
    (auth / "auth.json").write_text("{}")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return binary, auth, workspace


def test_native_structured_call_is_delivered_only_after_binding_and_guard(tmp_path):
    from contextlib import contextmanager

    from hearth.integrations.codex import app_server

    binary, auth, workspace = fake_cli(tmp_path)
    actions = []

    @contextmanager
    def guard():
        actions.append("guard")
        yield

    def call(params):
        assert actions == ["thread-1", "guard", "turn-1"]
        assert params["arguments"] == {"name": "Fictional reader"}
        actions.append(params["callId"])
        return {"contentItems": [{"type": "inputText", "text": "receipt-1"}], "success": True}

    result = app_server.run(
        binary=binary,
        auth_home=auth,
        workspace=workspace,
        prompt="Synthetic task",
        tools=[
            {
                "type": "function",
                "name": "hearth_probe",
                "description": "Provision fictional reader",
                "inputSchema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            }
        ],
        on_thread=lambda thread: actions.append(thread),
        on_turn=lambda thread, turn: actions.append(turn),
        on_tool=call,
        cancelled=lambda: False,
        dispatch_guard=guard,
        timeout=5,
    )
    assert actions == ["thread-1", "guard", "turn-1", "call-1"]
    assert result["launched"] is True
    assert result["error"] is None
    assert evidence(result).cost == 700
    assert evidence(result).output == "Fictional resident created."


def run_fixture(tmp_path, scenario="success", cli=None, **overrides):
    from contextlib import nullcontext

    from hearth.integrations.codex import app_server

    binary, auth, workspace = cli or fake_cli(tmp_path, scenario)
    options = dict(
        binary=binary,
        auth_home=auth,
        workspace=workspace,
        prompt="Synthetic task",
        tools=[
            {
                "type": "function",
                "name": "hearth_probe",
                "description": "Synthetic operation",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
        on_thread=lambda thread: None,
        on_turn=lambda thread, turn: None,
        on_tool=lambda params: {
            "contentItems": [{"type": "inputText", "text": "receipt-1"}],
            "success": True,
        },
        cancelled=lambda: False,
        dispatch_guard=nullcontext,
        timeout=5,
    )
    options.update(overrides)
    return app_server.run(**options)


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("unsafe_config", "app_server_configuration_unsafe"),
        ("instructions", "app_server_thread_unsafe"),
        ("foreign_call", "app_server_tool_request_invalid"),
        ("unknown_tool", "app_server_tool_request_invalid"),
    ],
)
def test_native_preflight_and_call_scope_refuse_without_dispatch(tmp_path, scenario, code):
    calls = []
    result = run_fixture(tmp_path, scenario, on_tool=lambda params: calls.append(params))
    assert calls == []
    assert result["error"] == code
    assert result["launched"] is (scenario in {"foreign_call", "unknown_tool"})


def test_revoked_dispatch_guard_stops_before_native_turn(tmp_path):
    from contextlib import contextmanager

    @contextmanager
    def revoked():
        raise Refused("management_revoked")
        yield

    result = run_fixture(tmp_path, dispatch_guard=revoked)
    assert result["launched"] is False
    assert result["error"] == "management_revoked"
    assert evidence(result).cost == 0


def test_cancelled_native_turn_is_reaped_and_keeps_unknown_usage(tmp_path):
    stopped = False

    def turn_started(thread, turn):
        nonlocal stopped
        stopped = True

    result = run_fixture(tmp_path, "stall", on_turn=turn_started, cancelled=lambda: stopped)
    assert result["launched"] is True
    assert result["cancelled"] is True
    assert result["exit_code"] is not None
    assert evidence(result).cost is None


def test_native_configuration_pins_are_checked_before_model_work(tmp_path):
    result = run_fixture(
        tmp_path, expected_pins={"catalog_sha256": "0" * 64, "tools_sha256": "1" * 64}
    )
    assert result["launched"] is False
    assert result["error"] == "app_server_configuration_changed"
    assert evidence(result).cost == 0


def test_an_extra_effective_file_grant_refuses_before_turn(tmp_path):
    result = run_fixture(tmp_path, "extra_file_grant")
    assert result["launched"] is False
    assert result["error"] == "app_server_configuration_unsafe"


def test_a_native_error_cannot_leave_a_known_price_after_recovery():
    result = native_result()
    result["events"].insert(
        -1,
        {
            "method": "error",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "error": {"message": "Synthetic lost provider reply"},
            },
        },
    )
    assert evidence(result).cost is None


@pytest.mark.parametrize("damage", ["envelope", "items", "message_text", "terminal_error"])
def test_malformed_native_result_is_refused_consistently(damage):
    result = native_result()
    if damage == "envelope":
        result = []
    elif damage == "items":
        result["events"][-1]["params"]["turn"]["items"] = None
    elif damage == "message_text":
        result["events"][-1]["params"]["turn"]["items"][0]["text"] = 17
    else:
        result["events"][-1]["params"]["turn"]["error"] = {"message": "Failed"}
    with pytest.raises(Refused, match="app_server_receipt_invalid"):
        evidence(result)


@pytest.mark.parametrize("scenario", ["reroute", "compact"])
def test_native_control_change_stops_before_management_call(tmp_path, scenario):
    calls = []
    result = run_fixture(tmp_path, scenario, on_tool=lambda params: calls.append(params))
    assert calls == []
    assert result["error"] == "app_server_execution_changed"
    assert evidence(result).cost is None


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("replay", None),
        ("conflict", "app_server_tool_request_conflict"),
        ("limit", "app_server_tool_limit"),
    ],
)
def test_repeated_native_call_cannot_repeat_side_effect(tmp_path, scenario, code):
    calls = []

    def call(params):
        calls.append(params)
        return {"contentItems": [{"type": "inputText", "text": "receipt-1"}], "success": True}

    result = run_fixture(tmp_path, scenario, on_tool=call, max_calls=1)
    assert len(calls) == 1
    assert result["error"] == code
    assert evidence(result).cost == (700 if code is None else None)


def test_policy_refusal_is_returned_to_native_model(tmp_path):
    def revoked(params):
        raise Refused("management_revoked")

    result = run_fixture(tmp_path, "refused", on_tool=revoked)
    assert result["error"] is None
    assert evidence(result).cost == 700


def test_native_timeout_reaps_process_and_preserves_unknown_usage(tmp_path):
    result = run_fixture(tmp_path, "stall", timeout=1)
    assert result["error"] == "app_server_timeout"
    assert result["exit_code"] is not None
    assert evidence(result).cost is None


@pytest.mark.parametrize("scenario", ["prose", "user_input"])
def test_prose_and_interactive_question_never_dispatch_management(tmp_path, scenario):
    calls = []
    result = run_fixture(tmp_path, scenario, on_tool=lambda params: calls.append(params))
    assert calls == []
    assert result["error"] is None
    assert evidence(result).status == "succeeded"
    assert evidence(result).cost == 700


def test_native_receipt_files_keep_private_bounded_storage(tmp_path):
    import os
    import stat

    from hearth.integrations.codex.app_server_transport import MAX_NATIVE_STREAM
    from hearth.integrations.codex.management_runtime import publish_receipt, read_receipt

    path = tmp_path / "native.json"
    value = {"synthetic": "😀" * 32_000}
    publish_receipt(path, value)
    assert read_receipt(path) == value
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        publish_receipt(path, value)
    with pytest.raises(ValueError, match="oversized native receipt"):
        publish_receipt(tmp_path / "oversized.json", {"synthetic": "x" * MAX_NATIVE_STREAM})
    assert not (tmp_path / "oversized.json").exists()
    os.link(path, tmp_path / "linked.json")
    with pytest.raises(ValueError, match="unsafe native receipt"):
        read_receipt(path)


def test_a_management_session_inside_a_sandbox_names_only_the_image_s_own_paths(tmp_path):
    """The worker's side stays on the host; the CLI's side is in the container.

    The transport does not move: it is still stdio over the pipe the launcher hands
    back, and the two processes this session starts are started the same way. What
    changes is every path in them -- the CLI, the login, the generated catalog and
    the working directory the session is told to work in.
    """
    from hearth.integrations.launcher import BINARIES, LOGIN, WORKSPACE, ContainerLauncher

    from tests import fake_docker

    digest = "sha256:" + "5" * 64
    image, network = "ghcr.io/hearth/sandbox@" + digest, "hearth-sandbox"
    daemon = tmp_path / "daemon"
    daemon.mkdir()
    docker = fake_docker.install(daemon)
    fake_docker.hold(docker, image=image, network=network)
    cli = fake_cli(tmp_path)
    fake_docker.carry(docker, BINARIES["codex_live_binary"], cli[0].read_bytes())
    result = run_fixture(
        tmp_path, cli=cli, launcher=ContainerLauncher(image, network, docker=str(docker))
    )
    assert result["launched"] is True and result["error"] is None, result["error"]
    assert evidence(result).cost == 700

    started = [call for call in fake_docker.calls(docker) if call[:1] == ["run"]]
    # Discovery and the session itself, both in containers of their own.
    assert len(started) == 2
    for argv in started:
        command = argv[argv.index(image) + 1 :]
        assert command[:2] == ["/usr/local/bin/codex", "app-server"]
        assert "CODEX_HOME=" + LOGIN in argv
        assert f"type=bind,source={cli[1]},target={LOGIN},readonly" in argv
        # The catalog Hearth generated for this session is mounted where it already
        # is, because the CLI reports that path back and the two have to agree.
        setting = next(part for part in command if part.startswith("model_catalog_json="))
        directory = setting.split('"')[1].rsplit("/", 1)[0]
        assert f"type=bind,source={directory},target={directory},readonly" in argv
        # The workspace the session is told to work in is the runtime's own tmpfs.
        assert argv[argv.index("--workdir") + 1] == WORKSPACE
        assert not any(str(cli[2]) in part for part in command)
