#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
#  Forge of Gods - offline game server emulator  (core)
#
#  Wire protocol, as recovered from Assembly-CSharp.dll:
#   * Requests : HTTP POST  /gs_api/<group>/<call>   body = gzip(JSON object of
#                string->string, always containing "post_test":"test").
#                Headers: X-GS-Cookie (device_identifer=..;account_type=..;
#                account_id=..;account_secure=..), secure_string (MD5 signature;
#                not verified here), X-GS-Accept (protocol/GSAPI.1, format/..).
#   * Replies  : "plist_amt" - NOT Apple plist.  Custom binary tree that starts
#                with the ASCII text SAS3_PBH + uint32, then typed elements
#                (see amt_encode).  The root is always a dict:
#                    ok    -> {"status": True,  "time": <int32>, "result": <any>}
#                    error -> {"status": False, "time": <int32>,
#                              "error": {"code": <native int>, "title", "msg", "trace"}}
#                Only profile/load is different: it answers with JSON text.
#   * Strings in AMT replies are ASCII only, integers are int32.
# ============================================================================
import os
import io
import re
import sys
import json
import gzip
import time
import base64
import struct
import hashlib
import random
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, quote
from xml.sax.saxutils import escape as _xml_escape

# ----------------------------------------------------------------------------
#  Settings
# ----------------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "fog_state.json")      # all saved game data
LOG_FILE = os.path.join(BASE_DIR, "fog_server.log")         # request log
CONFIG_DIR = os.path.join(BASE_DIR, "configs")              # optional: original config XML files
STATIC_DIR = os.path.join(BASE_DIR, "static")               # optional: files served under /static/
VERBOSE = False            # True = print every request body in full
FALLBACK_STEAM_ID = ""     # used only if the Steam ticket cannot be parsed
PERSIST = True             # tests switch this off so they never touch the real save file

# ----------------------------------------------------------------------------
#  Server error codes: client-side name -> native code the server must send
#  (extracted from GSErrorDescription.GetErrorCode in the DLL)
# ----------------------------------------------------------------------------
ERROR_CODES = {'ActionValidationError': 1400,
 'AddressFriendsMaxCountError': 70636,
 'AppleReceiptVerificationRequestError': 5400,
 'AppleVerificationDisabledError': 5500,
 'BankItemNotFound': 90003,
 'BankItemNotTicket': 90000,
 'BuildingAlreadyUpdated': 138031,
 'CannotGetGuildCastle': 138023,
 'CastleAlreadyUpdate': 138027,
 'ClientAppVersionIsGreaterError': 107,
 'ConfigNotFoundError': 1404,
 'CriticalActionValidationError': 1406,
 'CurrentOfficersCountIsCrowded': 1338274,
 'DidNotStartTournament': 13371400,
 'DiffPackNotFoundError': 1105,
 'EmailAlreadyInUseError': 96430,
 'FriendsLimitExceed': 40405,
 'GoogleReceiptVerificationRequestError': 5700,
 'GoogleVerificationDisabledError': 5600,
 'GuildContainsNotOnlyGuildMasterError': 13380042,
 'GuildFoundException': 13380001,
 'GuildHasActiveRaid': 13380077,
 'GuildHasNoActiveRaids': 13380046,
 'GuildHasNoCompletedRaids': 13380024,
 'GuildIntegrityException': 13380006,
 'GuildMemberRankIsNotValid': 1338829,
 'GuildNotEnoughResource': 13380019,
 'GuildNotFoundException': 13380000,
 'GuildWarExistException': 13380015,
 'GuildWarLimitException': 13380016,
 'GuildWasDisbandedBecauseOfInfluence': 138030,
 'IncorrectEmailError': 96429,
 'InternalValidationError': 282206,
 'InviteError': 40000,
 'InviteIncomingAlreadyExistsError': 40406,
 'InviteNotFoundError': 40404,
 'InviteOutgoingAlreadyExistsError': 40505,
 'LevelNotEnoughForTournament': 13371500,
 'MemberExceedException': 13380007,
 'MemberHasNoCompletedRaids': 13380091,
 'MemberRequestExistException': 13380004,
 'MemberRequestNotExistException': 13380020,
 'NewGuildMemberCannotDonate': 138032,
 'NewGuildMemberRankEqualOld': 1338583,
 'NoNextLevelException': 138028,
 'NotActiveBuilding': 138029,
 'NotEnoughContribution': 138026,
 'NotEnoughInfluence': 138025,
 'NotEnoughtMoneyForPurchase': 900082,
 'NotReachedParamsInQuery': 104,
 'OffersNotFoundError': 2015,
 'OffersProviderNotFoundError': 2014,
 'PVPMatchCanFinishError': 7006,
 'PVPMatchCanRevengeError': 7008,
 'PVPMatchCreateError': 7005,
 'PVPMatchDistributeRewardsError': 7010,
 'PVPMatchFinishError': 7007,
 'PVPMatchNotFoundError': 7002,
 'PVPMathFinishError': 7012,
 'PVPOpponentsNotFoundError': 7003,
 'PVPPlayerHasActiveMatch': 7004,
 'PVPPlayerNotFound': 7001,
 'PackNotFoundError': 1104,
 'PayloadUnserializeError': 903,
 'PlayerAlreadyInGuildException': 19423472,
 'PlayerNotInGuildException': 13380002,
 'ProfileBase64DecodeError': 705,
 'ProfileCommitError': 744,
 'ProfileIntegrityError': 708,
 'ProfileIsCheater': 709,
 'ProfileNotFoundError': 704,
 'ProfilePermissionError': 1338755,
 'ProfileUnserializeError': 706,
 'PvpMatchIntegrityException': 13380017,
 'QueryParamValidationError': 105,
 'RankPermissionException': 13380005,
 'RankProfileLessThenRankMember': 13381919,
 'RbkBuyExeption': 900083,
 'ReceiptAlreadyRegistered': 282202,
 'RedeemAlreadyUsed': 90004,
 'RelationAlreadyExists': 50505,
 'RequirementsException': 13380003,
 'RewardAlreadyAccepted': 90005,
 'RewardNotFoundError': 2004,
 'SaveSessionError': 904,
 'SelfPlayerAlreadyInGuildException': 15344917,
 'SelfPlayerNotInGuildException': 1532418,
 'SenderFriendsMaxCountError': 70635,
 'ServerMaintenanceError': 503,
 'ServiceDisabled': 282208,
 'ServiceUnavailable': 282207,
 'ShieldExists': 138033,
 'SocialPlayerError': 50000,
 'SocialPlayerNotFound': 50404,
 'StaticContentManagerError': 1100,
 'SteamBuyException': 900084,
 'SteamDeadlineException': 900086,
 'SteamFinalizeFailure': 900085,
 'StoredProfileRevisionGreaterError': 701,
 'StrictByAppVersionError': 106,
 'StrictByLevelError': 2006,
 'SwapWithYourselfException': 1233817,
 'TicketProcessingError': 90002,
 'TournamentNotFound': 13371404,
 'TournamentPlayersMigrationError': 13371409,
 'TransactionDoesNotExist': 90001,
 'UpgradeNotExists': 13381135,
 'WarlordAlreadyUsed': 50405,
 'WrongBundleId': 282203,
 'YouCannotAddMemberInGuild': 138021,
 'YouCannotJoinGuild': 138022}


class GSError(Exception):
    """Raise inside a handler to answer with an error reply.

    GSError("GuildNotFoundException", "no such guild")   # by client-side name
    GSError(13380000, "no such guild")                    # or by native code
    """

    def __init__(self, code, msg="", title=""):
        if isinstance(code, str):
            if code not in ERROR_CODES:
                raise KeyError("unknown GS error name %r" % (code,))
            self.name = code
            code = ERROR_CODES[code]
        else:
            self.name = str(code)
        Exception.__init__(self, "%s: %s" % (self.name, msg))
        self.code = int(code)
        self.msg = msg
        self.title = title


class Raw(object):
    """Return from a handler to bypass the AMT encoder (used for profile/load: JSON text)."""

    def __init__(self, body, content_type="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body
        self.content_type = content_type


# ----------------------------------------------------------------------------
#  AMT binary codec ("SAS3_PBH")
#    type byte: 0 bool(1B)  1 int32  2 float32  3 data(int32 len + bytes)
#               4 string(int32 len + ASCII bytes)  5 array(int32 count + elems)
#               6 dict(int32 count + [byte keylen, key chars, elem]...)
#    all integers little endian.
# ----------------------------------------------------------------------------
AMT_MAGIC = b"SAS3_PBH"
_INT_MIN, _INT_MAX = -2 ** 31, 2 ** 31 - 1


def _amt_enc(v, out):
    if v is None:
        v = ""
    if isinstance(v, bool):
        out += b"\x00" + (b"\x01" if v else b"\x00")
    elif isinstance(v, int):
        if v < _INT_MIN or v > _INT_MAX:
            log("!! integer %d does not fit the int32 wire format; sent as a string" % v)
            _amt_enc(str(v), out)
        else:
            out += b"\x01" + struct.pack("<i", v)
    elif isinstance(v, float):
        try:
            out += b"\x02" + struct.pack("<f", v)
        except OverflowError:
            out += b"\x02" + struct.pack("<f", float("inf") if v > 0 else float("-inf"))
    elif isinstance(v, (bytes, bytearray)):
        out += b"\x03" + struct.pack("<i", len(v)) + bytes(v)
    elif isinstance(v, str):
        b = v.encode("ascii", "replace")
        out += b"\x04" + struct.pack("<i", len(b)) + b
    elif isinstance(v, (list, tuple)):
        out += b"\x05" + struct.pack("<i", len(v))
        for item in v:
            _amt_enc(item, out)
    elif isinstance(v, dict):
        out += b"\x06" + struct.pack("<i", len(v))
        for k, item in v.items():
            kb = str(k).encode("ascii", "replace")
            if len(kb) > 255:
                raise ValueError("dict key longer than 255 bytes: %r" % (k,))
            out += struct.pack("<B", len(kb)) + kb
            _amt_enc(item, out)
    else:
        raise TypeError("cannot encode %s into AMT" % type(v).__name__)


def amt_encode(root):
    out = bytearray()
    out += AMT_MAGIC + struct.pack("<I", 0)      # the client ignores the size word
    _amt_enc(root, out)
    return bytes(out)


def amt_decode(data):
    """Python port of the client's Plist.Read (used by the tests)."""
    if data[:8] != AMT_MAGIC:
        raise ValueError("bad header %r" % (data[:8],))
    pos = [12]

    def take(n):
        if pos[0] + n > len(data):
            raise ValueError("truncated AMT data")
        b = data[pos[0]:pos[0] + n]
        pos[0] += n
        return b

    def elem():
        t = take(1)[0]
        if t == 0:
            return take(1)[0] != 0
        if t == 1:
            return struct.unpack("<i", take(4))[0]
        if t == 2:
            return struct.unpack("<f", take(4))[0]
        if t == 3:
            return take(struct.unpack("<i", take(4))[0])
        if t == 4:
            return take(struct.unpack("<i", take(4))[0]).decode("ascii", "replace")
        if t == 5:
            return [elem() for _ in range(struct.unpack("<i", take(4))[0])]
        if t == 6:
            d = {}
            for _ in range(struct.unpack("<i", take(4))[0]):
                kl = take(1)[0]
                key = take(kl).decode("utf-8", "replace")
                d[key] = elem()
            return d
        return None

    return elem()


# ----------------------------------------------------------------------------
#  Small helpers for handlers
# ----------------------------------------------------------------------------
def now():
    return int(time.time())


def md5hex(s):
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.md5(s).hexdigest()


def b64gz_encode(data):
    """bytes -> base64(gzip(bytes))   (the client's DataToBase64CompressedData)"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(gzip.compress(data)).decode("ascii")


def b64gz_decode(text):
    """base64(gzip(bytes)) -> bytes   (the client's UnpackNetworkData)"""
    return gzip.decompress(base64.b64decode(text))


def csv_split(text):
    """'a,b,c' -> ['a','b','c'] (the client's ListToString format); empty -> []"""
    return [p.strip() for p in str(text or "").split(",") if p.strip()]


def uid(n=10):
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        print(ascii(line), flush=True)
    if PERSIST:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ----------------------------------------------------------------------------
#  Persistent state:  STATE["players"][device_id] -> dict   STATE["world"] -> dict
#  Everything stored must be JSON serialisable (no bytes, no tuples as keys).
# ----------------------------------------------------------------------------
STATE = {"players": {}, "world": {}}
_LOCK = threading.RLock()


def new_player():
    return {
        "created": now(),
        "profile": None,           # the save data the client committed (a dict = parsed JSON), None = new player
        "revision": 0,
        "new_revision": 1000,
        "gems": 0, "contribution": 0, "paying": 0, "king_midas": 0,
        "dollars_spent": 0, "player_rank": 0,
        "friends": [], "invites": [],
    }


def load_state():
    global STATE
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        STATE = {"players": data.get("players", {}), "world": data.get("world", {})}
        log("loaded saved data: %d player(s)" % len(STATE["players"]))
    except FileNotFoundError:
        STATE = {"players": {}, "world": {}}
    except Exception as e:
        log("!! could not read %s (%s) - starting empty (old file kept as .bad)" % (STATE_FILE, e))
        try:
            os.replace(STATE_FILE, STATE_FILE + ".bad")
        except Exception:
            pass
        STATE = {"players": {}, "world": {}}


def save_state():
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(STATE, f)
    os.replace(tmp, STATE_FILE)


def _digest():
    return hashlib.md5(json.dumps(STATE, sort_keys=True).encode("utf-8")).digest()


# ----------------------------------------------------------------------------
#  Routing
# ----------------------------------------------------------------------------
ROUTES = {}          # "friends/get_friend_list" -> function(ctx)
PREFIX_ROUTES = []   # [("validate_action/", function)]  (registered with a trailing slash)


def _norm(path):
    p = path.split("?", 1)[0].strip("/")
    if p.startswith("gs_api/"):
        p = p[len("gs_api/"):]
    return p


def route(*paths):
    """@route("group/call")  - path is relative to /gs_api/.  A trailing slash
    ("validate_action/") registers a prefix route that also matches "validate_action/xyz"."""
    def deco(fn):
        for p in paths:
            if p.endswith("/"):
                PREFIX_ROUTES.append((_norm(p) + "/", fn))
            else:
                ROUTES[_norm(p)] = fn
        return fn
    return deco


class Ctx(object):
    """What a handler gets.

    path       normalised call name, e.g. "friends/get_friend_list"
    params     dict of request parameters (str -> str, 'post_test' removed)
    player     persistent dict of the calling account (mutate freely; auto-saved)
    world      persistent dict shared by all accounts (auto-saved)
    device_id  value of device_identifer from X-GS-Cookie (e.g. "Steam7656...")
    steam_id   Steam id remembered from profile/cst ("" if unknown)
    now        current unix time (int)
    """

    def __init__(self, path, params, headers, cookie):
        self.path = path
        self.params = params
        self.headers = headers
        self.cookie = cookie
        self.now = now()
        self.device_id = cookie.get("device_identifer", "")
        self.account_id = cookie.get("account_id", "")
        if self.device_id and self.device_id != "not_inited":
            self.player = STATE["players"].setdefault(self.device_id, new_player())
        else:
            self.player = new_player()          # throw-away
        self.world = STATE["world"]

    @property
    def steam_id(self):
        return self.world.get("steam_id", "") or FALLBACK_STEAM_ID

    def player_of(self, device_id, create=False):
        """Saved data of any account by its id (None if unknown, unless create=True)."""
        if create:
            return STATE["players"].setdefault(device_id, new_player())
        return STATE["players"].get(device_id)

    def all_players(self):
        """dict: account id -> player dict, for every account on this server."""
        return STATE["players"]

    def param(self, name, default=""):
        v = self.params.get(name)
        return default if v is None else str(v)

    def int_param(self, name, default=0):
        try:
            return int(float(self.param(name, str(default))))
        except ValueError:
            return default

    def csv_param(self, name):
        return csv_split(self.param(name))


def _ok(result):
    return {"status": True, "time": now(), "result": {} if result is None else result}


def _err(e):
    return {"status": False, "time": now(),
            "error": {"code": e.code, "title": e.title, "msg": e.msg, "trace": "no_trace"}}


def _parse_cookie(text):
    out = {}
    for part in (text or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _short(params):
    items = []
    for k, v in params.items():
        s = str(v)
        if len(s) > 48:
            s = s[:45] + "...(%d)" % len(s)
        items.append("%s=%s" % (k, s))
    return " ".join(items)


def handle(path, params, headers):
    """Run one call.  Returns (http_status, content_type, body_bytes)."""
    key = _norm(path)
    cookie = _parse_cookie(headers.get("x-gs-cookie", ""))
    params = dict(params)
    params.pop("post_test", None)
    with _LOCK:
        before = _digest() if PERSIST else None
        fn = ROUTES.get(key)
        if fn is None:
            for prefix, pfn in PREFIX_ROUTES:
                if key.startswith(prefix) or key + "/" == prefix:
                    fn = pfn
                    break
        ctx = Ctx(key, params, headers, cookie)
        status = "ok"
        try:
            if fn is None:
                log("!! UNHANDLED gs_api/%s  %s   (answered with an empty result)" % (key, _short(params)))
                reply, ctype = _ok({}), None
            else:
                res = fn(ctx)
                if isinstance(res, Raw):
                    reply, ctype = res, res.content_type
                else:
                    reply, ctype = _ok(res), None
        except GSError as e:
            reply, ctype, status = _err(e), None, "ERR %s (%d) %s" % (e.name, e.code, e.msg)
        except Exception as e:                                  # a bug in a handler
            log("!! handler for gs_api/%s crashed:\n%s" % (key, traceback.format_exc()))
            reply, ctype, status = _err(GSError("ServiceUnavailable", "emulator error: %s" % e)), None, "CRASH"
        if isinstance(reply, Raw):
            body = reply.body
        else:
            body = amt_encode(reply)
            ctype = "application/octet-stream"
        if fn is not None:
            log("gs_api/%-34s %s -> %s (%d B)" % (key, _short(params) if VERBOSE or len(_short(params)) < 160 else _short(params)[:157] + "...", status, len(body)))
        if PERSIST and _digest() != before:
            save_state()
    return 200, ctype, body


def call(path, params=None, device="SteamTEST1", cookie_extra=None, headers=None):
    """Test helper: run a call in-process and decode the reply exactly like the client does.
    Returns the decoded envelope dict (AMT) or the parsed JSON (Raw/JSON replies)."""
    global PERSIST
    PERSIST = False
    hdrs = {"x-gs-cookie": "device_identifer=%s;account_type=guest;account_id=guest;account_secure=x" % device}
    if cookie_extra:
        hdrs["x-gs-cookie"] += ";" + cookie_extra
    if headers:
        hdrs.update(headers)
    status, ctype, body = handle(path, params or {}, hdrs)
    if body[:8] == AMT_MAGIC:
        return amt_decode(body)
    return json.loads(body.decode("utf-8"))


def reset_test_state():
    global STATE
    STATE = {"players": {}, "world": {}}


# ----------------------------------------------------------------------------
#  HTTP server
# ----------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    server_version = "FOGMock/1.0"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):      # silence the default access log
        pass

    def _send(self, status, ctype, body):
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        if path.startswith("/static/"):
            name = os.path.normpath(path[len("/static/"):]).lstrip("\\/")
            full = os.path.join(STATIC_DIR, name)
            if not os.path.abspath(full).startswith(os.path.abspath(STATIC_DIR)) or not os.path.isfile(full):
                return self._send(404, "text/plain", b"not found")
            with open(full, "rb") as f:
                return self._send(200, "application/octet-stream", f.read())
        text = "Forge of Gods offline server is running (%d calls implemented)\n" % len(ROUTES)
        self._send(200, "text/plain", text.encode("ascii"))

    do_HEAD = do_GET

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            params = {}
            if raw:
                try:
                    obj = json.loads(raw.decode("utf-8"))
                    if isinstance(obj, dict):
                        params = obj
                except Exception:
                    params = dict(parse_qsl(raw.decode("utf-8", "replace")))
            headers = {k.lower(): v for k, v in self.headers.items()}
            if VERBOSE:
                log("   raw body: %r" % (raw[:2000],))
            status, ctype, body = handle(self.path, params, headers)
        except Exception:
            log("!! request failed:\n%s" % traceback.format_exc())
            status, ctype, body = 500, "text/plain", b"error"
        self._send(status, ctype, body)


def main():
    load_state()
    try:
        srv = ThreadingHTTPServer((HOST, PORT), _Handler)
    except OSError as e:
        print("Could not open %s:%d (%s). Is another copy of the server still running?" % (HOST, PORT, e))
        return
    srv.daemon_threads = True
    print("=" * 64)
    print(" Forge of Gods - offline server")
    print("=" * 64)
    print(" listening on http://%s:%d   (%d calls implemented)" % (HOST, PORT, len(ROUTES) + len(PREFIX_ROUTES)))
    print(" saved data : %s" % STATE_FILE)
    print(" request log: %s" % LOG_FILE)
    report = globals().get("startup_report")
    if report:
        try:
            report()
        except Exception:
            pass
    print(" Ctrl+C to stop\n", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if PERSIST:
            try:
                save_state()
            except Exception:
                pass
        print("stopped")



# ============================================================================
#  CORE CALLS - everything the client needs to start up, log in, download its
#  data files and load / save the player profile.
#  (all reply shapes below were read out of the client code in the DLL)
# ============================================================================

STEAM_SALT = "0117vasya"                 # SteamScript.CheckBd in the DLL
STEAM64_BASE = REPLACE_WITH_YOUR_STEAM_ID         # first individual Steam account id
EXPECTED_CONFIGS = [
    "Actions", "Attack_Effects", "Awake", "Bank_Items", "Boosters", "Bundles", "Camps", "Castle",
    "CastleResearchItems", "ChatFilter", "ChatSmiley", "Constants", "CurAppVersion", "DayRewards",
    "DecorData", "Dialogs", "Echelons", "Events", "GuildBossRaids", "GuildCastle",
    "GuildEchelonsRewards", "GuildPlaceRewards", "GuildSector", "GuildShop", "Guilds", "Holidays",
    "InviteRewardMonsters", "Items", "Localization", "Locations_Rows", "MapChests", "MapDialogs",
    "Monsters", "Monsters_Growth", "PlayerData", "PvPMatching", "PvP_Rewards", "Quests",
    "RandomChests", "RedeemData", "RequiredAppVersion", "Runes", "Sets", "Skins", "SpeechBubbles",
    "SteamDLC", "Strenght", "Summon_Items", "Tips", "Trials", "Tutorial", "UnlockTiles",
    "Unlock_Items", "Vip", "Warlords"]


# ----------------------------------------------------------------------------
#  configs/ping   - "is the server alive / is my version ok"
# ----------------------------------------------------------------------------
@route("configs/ping")
def configs_ping(ctx):
    return {}


# ----------------------------------------------------------------------------
#  profile/cst    - Steam login check
#  The client sends its Steam auth ticket (hex).  It only continues loading when
#  the server answers  md5(<steam id> + <ticket hex> + "0117vasya").
# ----------------------------------------------------------------------------
def steam_id_from_ticket(hex_ticket):
    """A Steam auth ticket starts: [4B length][8B token][8B SteamID64, little endian]..."""
    try:
        raw = bytes.fromhex(hex_ticket)
    except ValueError:
        return ""
    if len(raw) < 20:
        return ""
    sid = struct.unpack_from("<Q", raw, 12)[0]
    if STEAM64_BASE < sid < STEAM64_BASE + 2 ** 32:
        return str(sid)
    return ""


@route("profile/cst")
def profile_cst(ctx):
    ticket = ctx.param("param42")
    sid = steam_id_from_ticket(ticket)
    how = "from the Steam ticket"
    if not sid:
        m = re.match(r"^Steam(\d{15,20})$", ctx.device_id or "")
        sid = m.group(1) if m else ""
        how = "from the cookie"
    if not sid and FALLBACK_STEAM_ID:
        sid, how = FALLBACK_STEAM_ID, "from FALLBACK_STEAM_ID"
    if sid:
        ctx.world["steam_id"] = sid
        log("   Steam login: Steam ID %s (%s)" % (sid, how))
    else:
        log("!! profile/cst: could not work out your Steam ID - the game will wait forever at the "
            "login screen.  Put your 17-digit Steam ID into FALLBACK_STEAM_ID at the top of server.py")
    return {"steamid": md5hex(sid + ticket + STEAM_SALT)}


# ----------------------------------------------------------------------------
#  Account ids: get_parent / profiles_preview / merge_profiles / get_accs_outside_stat
#  The client sends all of its login ids (here: just "Steam<id>") and wants to know which
#  single account each one belongs to ("parent").  An id nobody merged is its own parent.
# ----------------------------------------------------------------------------
def _parent_of(world, acc):
    parents = world.setdefault("parents", {})
    seen = set()
    while acc in parents and acc not in seen:
        seen.add(acc)
        acc = parents[acc]
    return acc


@route("profile/get_parent")
def profile_get_parent(ctx):
    ids = ctx.csv_param("ids")
    rows = "".join("<ParentKey><source>%s</source><parent>%s</parent></ParentKey>"
                   % (_xml_escape(i), _xml_escape(_parent_of(ctx.world, i))) for i in ids)
    xml = '<?xml version="1.0" encoding="utf-8"?><DocumentElement>%s</DocumentElement>' % rows
    return b64gz_encode(xml)


@route("profile/profiles_preview")
def profile_profiles_preview(ctx):
    # only asked when one player has several different logins (e.g. Facebook + Steam).
    # "no preview" makes the client simply merge them into one account.
    return {}


@route("profile/merge_profiles")
def profile_merge_profiles(ctx):
    parent = ctx.param("parent")
    for src in ctx.csv_param("sources"):
        if src and src != parent:
            ctx.world.setdefault("parents", {})[src] = parent
    return parent


@route("profile/get_accs_outside_stat")
def profile_get_accs_outside_stat(ctx):
    return {}


# ----------------------------------------------------------------------------
#  profile/load  - hand the saved game back to the client (as JSON text, not AMT)
#  profile/commit - the client saves its game (whole, or only some top-level fields)
#
#  The profile is kept as a normal Python dict (the JSON the client committed).
#  The client's parser needs exactly  "data_format": "json"  and  "data": {"  in the text
#  (see Account.OnProfileLoaded) - json.dumps with its default separators gives that.
# ----------------------------------------------------------------------------
def _json_error_reply(ctx, name, msg):
    return Raw(json.dumps({"status": "error", "time": ctx.now,
                           "error": {"code": ERROR_CODES[name], "title": name, "msg": msg,
                                     "trace": "no_trace"}}))


@route("profile/load")
def profile_load(ctx):
    p = ctx.player
    prof = p.get("profile")
    if not isinstance(prof, dict) or not prof:
        # a brand new player: the client reacts to this by creating a fresh profile itself
        log("   no saved game for %s - the client will start a new one" % (ctx.device_id or "?"))
        return _json_error_reply(ctx, "ProfileNotFoundError", "profile not found")
    result = {"data_format": "json",
              "revision": int(p.get("revision", 0)),
              "new_revision": int(p.get("new_revision", 1000)),
              "king_midas": int(p.get("king_midas", 0)),
              "paying": int(p.get("paying", 0)),
              "gems": int(p.get("gems", 0)),
              "contribution": int(p.get("contribution", 0)),
              "friends": [], "invites": [],          # must stay empty (see AmtClient.OnAccountUpdated)
              "data": prof}
    return Raw(json.dumps({"status": "ok", "time": ctx.now, "error": {}, "ip": "127.0.0.1",
                           "result": result}))


@route("profile/commit")
def profile_commit(ctx):
    p = ctx.player
    text = ctx.param("data")
    if not text:
        raise GSError("ProfileUnserializeError", "no data")
    if ctx.param("data_format", "json") != "json":                # never sent by this client build
        try:
            text = b64gz_decode(text).decode("utf-8")
        except Exception:
            raise GSError("ProfileBase64DecodeError", "cannot decode data")
    try:
        obj = json.loads(text)
    except ValueError:
        raise GSError("ProfileUnserializeError", "data is not valid JSON")
    if not isinstance(obj, dict) or not obj:
        raise GSError("ProfileUnserializeError", "data is not a JSON object")

    partial = ctx.param("partial")
    if partial:
        stored = p.get("profile")
        if not isinstance(stored, dict) or not stored:
            log("!! partial save received but no full save is stored for %s - ignored" % ctx.device_id)
            raise GSError("ProfileNotFoundError", "no stored profile to update")
        stored.update(obj)                      # a partial save lists only the changed top-level fields
        p["profile"] = stored
    else:
        p["profile"] = obj

    p["revision"] = ctx.int_param("revision", int(p.get("revision", 0)) + 1)
    p["new_revision"] = int(p.get("new_revision", 1000)) + 1
    p["paying"] = ctx.int_param("paying", 0)
    p["dollars_spent"] = ctx.int_param("dollars_spent", 0)
    p["player_rank"] = ctx.int_param("player_rank", 0)
    p["saved_at"] = ctx.now
    return {"revision": p["revision"], "new_revision": p["new_revision"]}


@route("profile/clone")
def profile_clone(ctx):
    return {}                                   # never called by the client (Account.Clone is empty)


@route("profile/check_migrate")
def profile_check_migrate(ctx):
    """Copy another account's save onto this one (used when logins are linked)."""
    old_id = ctx.param("old_account_id")
    old = ctx.player_of(old_id)
    force = ctx.param("force_rewrite") == "1"
    if old and old_id != ctx.device_id and isinstance(old.get("profile"), dict) and old["profile"]:
        mine = ctx.player
        if force or not mine.get("profile"):
            mine["profile"] = json.loads(json.dumps(old["profile"]))
            mine["revision"] = old.get("revision", 0)
            log("   migrated the save of %s onto %s" % (old_id, ctx.device_id))
    return {}


@route("profile/request_reset")
def profile_request_reset(ctx):
    """'Wipe profile' - the client sends md5(<account id> + ':_foG:1337')."""
    expect = md5hex("%s:_foG:1337" % ctx.device_id)
    if ctx.param("reset_id") != expect:
        log("   (reset code did not match, resetting anyway - this is your own local server)")
    ctx.player["profile"] = None
    ctx.player["revision"] = 0
    log("   save of %s wiped" % ctx.device_id)
    return {}


# ----------------------------------------------------------------------------
#  validate_action/<ActionType>   - the real server checked the player's actions for cheating
#  and the client only needs to hear "ok" (an empty dict).
# ----------------------------------------------------------------------------
@route("validate_action/")
def validate_action(ctx):
    return {}


# ----------------------------------------------------------------------------
#  Game data files ("configs") - the client asks for any it does not have yet.
#  Put the original files into the  configs  folder next to server.py:
#      configs/Items.xml     or     configs/Items.zip  (the client's own cache format)
#  The client keeps its own copy in  %USERPROFILE%\AppData\LocalLow\<company>\<game>\XML\
#  so once it has them, it does not ask again.
# ----------------------------------------------------------------------------
def _config_files():
    """name -> full path, for every file the user put into the configs folder"""
    out = {}
    try:
        names = sorted(os.listdir(CONFIG_DIR))
    except OSError:
        return out
    for fn in names:
        full = os.path.join(CONFIG_DIR, fn)
        base, ext = os.path.splitext(fn)
        if os.path.isfile(full) and base and ext.lower() in ("", ".xml", ".zip", ".gz", ".txt"):
            out.setdefault(base, full)
    return out


def _config_bytes(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":                  # the client's cache files are gzip data named .zip
        raw = gzip.decompress(raw)
    return raw


@route("configs/check_updates")
def configs_check_updates(ctx):
    have = {k: v for k, v in ctx.params.items() if k != "data_format"}      # name -> md5 the client holds
    files = _config_files()
    out, sent = {}, 0
    for name, path in files.items():
        try:
            raw = _config_bytes(path)
        except Exception as e:
            log("!! could not read config %s: %s" % (path, e))
            continue
        if have.get(name) == hashlib.md5(raw).hexdigest():
            continue
        out[name] = b64gz_encode(raw)
        sent += 1
    missing = [n for n in EXPECTED_CONFIGS if n not in files and n not in have]
    log("   configs: client holds %d, server folder has %d, sending %d" % (len(have), len(files), sent))
    if missing:
        log("!! the game needs %d data file(s) that neither the client nor the 'configs' folder has: %s"
            % (len(missing), ", ".join(missing[:12]) + (" ..." if len(missing) > 12 else "")))
        log("!! copy them into %s  (see README)" % CONFIG_DIR)
    return out


# ----------------------------------------------------------------------------
#  static_content/check_updates_no_diff - list of art "asset bundles" the client may download.
#  Every file in the  static  folder is offered under its file name (without .unity3d).
# ----------------------------------------------------------------------------
@route("static_content/check_updates_no_diff")
def static_content_list(ctx):
    host = ctx.headers.get("host") or ("%s:%d" % (HOST, PORT))
    out = []
    try:
        names = sorted(os.listdir(STATIC_DIR))
    except OSError:
        names = []
    for fn in names:
        full = os.path.join(STATIC_DIR, fn)
        if not os.path.isfile(full):
            continue
        base = fn[:-len(".unity3d")] if fn.lower().endswith(".unity3d") else fn
        rev = int(os.path.getmtime(full)) % 2147483647
        out.append({"name": base,
                    "url": "http://%s/static/%s?v=%d" % (host, quote(fn), rev),
                    "size": min(os.path.getsize(full), 2147483647),
                    "revision": rev})
    return out


# ----------------------------------------------------------------------------
#  Fallback for the startup reward check (the economy module replaces this when present)
# ----------------------------------------------------------------------------
@route("rewards/get_rewards")
def rewards_get_rewards_fallback(ctx):
    return {"count": 0}


# ----------------------------------------------------------------------------
#  Start-up report: tells the user at once whether the game data is available.
# ----------------------------------------------------------------------------
def find_client_cache():
    """Look for the game's own downloaded config cache on this PC (Windows)."""
    import glob
    found = []
    home = os.path.expanduser("~")
    for pattern in (os.path.join(home, "AppData", "LocalLow", "*", "*", "XML"),
                    os.path.join(home, "AppData", "LocalLow", "*", "XML")):
        for d in glob.glob(pattern):
            try:
                n = len([f for f in os.listdir(d) if f.lower().endswith(".zip")])
            except OSError:
                continue
            if n:
                found.append((d, n))
    return found


def startup_report():
    files = _config_files()
    print(" game data folder  : %s  (%d files)" % (CONFIG_DIR, len(files)))
    try:
        n_static = len([f for f in os.listdir(STATIC_DIR) if os.path.isfile(os.path.join(STATIC_DIR, f))])
    except OSError:
        n_static = 0
    print(" art bundle folder : %s  (%d files)" % (STATIC_DIR, n_static))
    try:
        cache = find_client_cache()
    except Exception:
        cache = []
    if cache:
        for d, n in cache:
            print(" game's own cache  : %s  (%d config files)" % (d, n))
        print("   -> the game already holds its data files; the 'configs' folder can stay empty.")
    elif not files:
        print(" NOTE: no game data files found (neither the game's cache nor the 'configs' folder).")
        print("       The game cannot start without them - see README.txt.")
    print()


if __name__ == "__main__":
    main()
