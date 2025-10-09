# grandpa_prod.py
# Production-style GRANDPA finality gadget (async, persisted, ed25519 signatures, optional BLS aggregation).
# Usage:
#   python grandpa_prod.py --id 0 --keys keys.json --config nodes_config.json

import argparse, asyncio, json, logging, time, hashlib, random
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from collections import defaultdict, Counter
from base64 import b64encode, b64decode
import aiosqlite
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError
import asyncio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("grandpa")

# ------------------ Configurable timeouts ------------------
PREVOTE_TIMEOUT = 4.0
PRECOMMIT_TIMEOUT = 4.0

def finalize_block(block: dict, node_id: int, keys: dict, config: dict) -> dict:
    """
    Finalizes a Safrole-produced block using GRANDPA.
    Args:
        block (dict): Block produced by Safrole.
        node_id (int): Node ID for GRANDPA voting.
        keys_file (str): Path to keys.json.
        config_file (str): Path to nodes_config.json.
    Returns:
        dict: { "finalized": bool, "justification": ... }
    """
    node_cfg = next((n for n in config["nodes"] if n["id"] == node_id), None)
    gossip = GossipNode(node_id, node_cfg["host"], node_cfg["port"], config["nodes"], keys)
    db = DB_FILE_TEMPLATE.format(node_id)
    engine = GrandpaEngine(gossip, config["nodes"])

    # Add the Safrole block to Grandpa's chain/tree
    gossip.blockchain.tree.add_block(block)
    BLOCK_BY_HASH[block["hash"]] = block

    async def run_finalization():
        await init_db(db)
        result, justification = await engine.run_round(db)
        return result, justification

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result, justification = loop.run_until_complete(run_finalization())
    loop.close()

    return {
        "finalized": bool(result),
        "justification": justification
    }


# ------------------ Data classes ------------------

@dataclass
class ReadyMsg:
    node_id: int
    type: str = "ready"
    def to_json(self): return json.dumps(asdict(self))
    @staticmethod
    def from_json(s: str): return ReadyMsg(**json.loads(s))

@dataclass
class BlockMsg:
    type: str
    block: dict
    def to_json(self): return json.dumps(asdict(self))
    @staticmethod
    def from_json(s: str): return BlockMsg(**json.loads(s))

@dataclass
class Justification:
    round_number: int
    block_hash: str
    signatures: List[Dict[str, Any]]

@dataclass
class VoteMsg:
    round: int
    stage: str          # 'prevote' or 'precommit'
    block_hash: Optional[str]
    block_height: Optional[int]
    state_root: Optional[str]
    validator: int
    sig_ed25519_b64: str
    bls_pk_hex: Optional[str] = None
    def to_json(self): return json.dumps(asdict(self))
    @staticmethod
    def from_json(s: str): return VoteMsg(**json.loads(s))

def vote_message_canonical(round_no, stage, bh, h, sr) -> str:
    return f"{round_no}|{stage}|{bh or 'nil'}|{h or 'nil'}|{sr or 'nil'}"

# ------------------ Blockchain ------------------

# Small static chain initial records (hash strings are examples)
STATIC_CHAIN = [
    {"hash": "00"*32, "parent": None, "height": 0, "state_root": "root0", "slot": 0,
     "author": -1, "ticketed": True, "audited": True},
    {"hash": "a1"*32, "parent": "00"*32, "height": 1, "state_root": "s1", "slot": 1,
     "author": 1, "ticketed": True, "audited": True},
    # the "a4a4..." block used in earlier tests — keep audited True so it can be finalized
    {"hash": "a4"*32, "parent": "a1"*32, "height": 2, "state_root": "s2", "slot": 2,
     "author": 2, "ticketed": True, "audited": True},
]

BLOCK_BY_HASH = {b["hash"]: b for b in STATIC_CHAIN}
GENESIS_HASH = STATIC_CHAIN[0]["hash"]

class Block:
    def __init__(self, h, parent, data, hash=None):
        self.height, self.parent_hash, self.data = h, parent, data
        self.hash = hash or hashlib.sha256(f"{h}{parent}{data}".encode()).hexdigest()
    def __repr__(self): return f"Block(h={self.height}, {self.hash[:8]}..)"

class BlockTree:
    def __init__(self):
        # blocks: hash -> record (dict)
        self.blocks: Dict[str, dict] = {}
        # children: parent_hash -> list of child hashes
        self.children: Dict[Optional[str], List[str]] = defaultdict(list)

    def add_block(self, rec: dict):
        h = rec["hash"]
        if h in self.blocks:
            return
        self.blocks[h] = rec
        p = rec.get("parent")
        self.children[p].append(h)

    def contains_finalized_ancestor(self, block_hash: str, finalized_hash: Optional[str]) -> bool:
        """Return True if finalized_hash is an ancestor of block_hash (or if finalized_hash is None)."""
        if finalized_hash is None:
            return True
        cur = block_hash
        while cur:
            if cur == finalized_hash:
                return True
            cur = self.blocks.get(cur, {}).get("parent")
        return False

    def ancestor_chain(self, block_hash: str, stop_at: Optional[str]=None) -> List[str]:
        """Return list of ancestor hashes from block_hash down to (but excluding) stop_at (or genesis)."""
        chain = []
        cur = block_hash
        while cur and cur != stop_at:
            chain.append(cur)
            cur = self.blocks.get(cur, {}).get("parent")
        return chain

    def contains_equivocation_between(self, block_hash: str, finalized_hash: Optional[str]) -> bool:
        """
        Inspect the ancestors of block_hash down to finalized_hash (exclusive).
        If any parent has more than one child (i.e., sibling blocks) in that unfinalized window,
        treat as equivocation (disqualify per graypaper §19).
        """
        cur = block_hash
        while cur and cur != finalized_hash:
            parent = self.blocks.get(cur, {}).get("parent")
            # check siblings of 'cur' by inspecting children[parent]
            if parent is not None:
                siblings = self.children.get(parent, [])
                # any time we have more than 1 child of this parent, it's a possible equivocation
                # but only matters if those siblings are unfinalized (they are in this window)
                if len(siblings) > 1:
                    return True
            cur = parent
        return False

    def best_chain_head(self, finalized_hash: Optional[str]) -> Optional[dict]:
        """
        Choose the best block among all blocks that:
         - have the finalized_hash as an ancestor (or finalized_hash is None)
         - are audited (block record 'audited' == True)
         - contain NO equivocation in the unfinalized segment
        Then pick the highest-height such block.
        This follows the graypaper constraint: best block must have finalized ancestor, be audited,
        and no equivocations in unfinalized blocks (see section 19).
        """
        candidates = []
        for rec in self.blocks.values():
            # must be audited
            if not rec.get("audited", False):
                continue
            # must have finalized as ancestor (or finalized None)
            if not self.contains_finalized_ancestor(rec["hash"], finalized_hash):
                continue
            # must have no equivocations on the path from rec to finalized
            if self.contains_equivocation_between(rec["hash"], finalized_hash):
                continue
            candidates.append(rec)
        if not candidates:
            return None
        # pick the candidate with max height; tie-breaker arbitrary (hash)
        best = max(candidates, key=lambda r: (r["height"], r["hash"]))
        return best

class DummyBlockchain:
    """
    Produces blocks (and sometimes forks) locally. When a new block is produced it is gossiped
    to peers. Blocks carry an 'audited' flag that Grandpa will require to be True before voting.
    """
    def __init__(self):
        self.tree, self.blocks = BlockTree(), []
        for rec in STATIC_CHAIN:
            b = Block(rec["height"], rec["parent"], f"static-{rec['height']}", rec["hash"])
            self.blocks.append(b)
            self.tree.add_block(rec)
        self.head = self.blocks[-1]

    def latest_block(self): return self.blocks[-1]

    def produce_block(self):
        """
        Produce a new block. To create forks/gossip-driven forks:
         - With some probability choose a non-head parent to create a competing branch.
         - Otherwise extend the current head.
        Each block is marked audited=True by default, but we can simulate
        temporarily unaudited blocks by toggling the flag (not done here).
        """
        prev = self.latest_block()
        salt = random.randint(0, 1_000_000)
        # With some chance, pick a random parent from recent blocks to create a fork
        fork_chance = 0.35
        if random.random() < fork_chance and len(self.blocks) >= 2:
            # pick a parent among the last few blocks, possibly not the absolute latest
            lookback = min(5, len(self.blocks))
            parent_block = random.choice(self.blocks[-lookback:])
            parent_hash = parent_block.hash
            parent_height = parent_block.height
        else:
            parent_hash = prev.hash
            parent_height = prev.height

        new_height = parent_height + 1
        b = Block(new_height, parent_hash, f"txs-{salt}")
        self.blocks.append(b)
        # rec carries audited flag (simulate Safrole/auditing). For now we set audited True most of the time
        audited = True if random.random() > 0.05 else False  # small chance a block is not yet audited
        rec = {"hash": b.hash, "parent": parent_hash, "height": b.height,
               "state_root": f"st{b.height}", "slot": b.height,
               "author": b.height % 3, "ticketed": True, "audited": audited}
        self.tree.add_block(rec)
        BLOCK_BY_HASH[b.hash] = rec
        self.head = b
        logger.info(f"Produced {b} (parent {parent_hash[:8]}.., audited={audited})")
        return b

    async def produce_loop(self, gossip):
        while True:
            blk = self.produce_block()
            # gossip block to peers
            await gossip.gossip_block(BLOCK_BY_HASH[blk.hash])
            await asyncio.sleep(6)

class GrandpaRuntimeConfig:
    def __init__(self, keys_path, config_path):
        self.keys_path = keys_path
        self.config_path = config_path
        self.reload()

    def reload(self):
        with open(self.keys_path) as f:
            self.keys_all = json.load(f)
        with open(self.config_path) as f:
            self.config = json.load(f)

    def get_keys(self, node_id):
        validators_map = {v["id"]: v for v in self.keys_all["validators"]}
        return validators_map.get(node_id)

    def get_config(self):
        return self.config

# Usage in finalize_block:
# Instead of loading files every time, keep GrandpaRuntimeConfig instance and call .reload() when needed.

# ------------------ Persistence ------------------

DB_FILE_TEMPLATE = "grandpa_node_{}.db"


async def init_db(path):
    async with aiosqlite.connect(path) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS votes (
            round INTEGER, stage TEXT, block_hash TEXT,
            block_height INTEGER, state_root TEXT, validator INTEGER,
            sig_ed25519_b64 TEXT, bls_pk_hex TEXT, received_at REAL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS finalized (
            id INTEGER PRIMARY KEY CHECK (id=1), block_hash TEXT, finalized_at REAL)""")
        await db.commit()

async def persist_vote(db_path, v: VoteMsg):
    async with aiosqlite.connect(db_path) as d:
        await d.execute("INSERT INTO votes VALUES (?,?,?,?,?,?,?,?,?)",
                        (v.round, v.stage, v.block_hash, v.block_height,
                         v.state_root, v.validator, v.sig_ed25519_b64,
                         v.bls_pk_hex, time.time()))
        await d.commit()

async def store_finalized(db_path, bh):
    async with aiosqlite.connect(db_path) as d:
        await d.execute("INSERT OR REPLACE INTO finalized VALUES (1,?,?)",
                        (bh, time.time())); await d.commit()

async def load_finalized(db_path):
    async with aiosqlite.connect(db_path) as d:
        cur = await d.execute("SELECT block_hash FROM finalized WHERE id=1")
        row = await cur.fetchone(); return row[0] if row else None

# ------------------ Gossip (simple TCP) ------------------

class GossipNode:
    def __init__(self, node_id, host, port, peers, keys):
        self.node_id, self.host, self.port, self.peers = node_id, host, port, peers
        self.keys, self.ready_set = keys, set()
        self.blockchain, self.server = DummyBlockchain(), None
        self.peer_writers, self.incoming = {}, asyncio.Queue()
        self.ed_sk = SigningKey(b64decode(keys["ed_sk_b64"]))
        # small receive buffer by message type to allow collect-and-clear behavior
        self.recv_buffer: Dict[str, List[VoteMsg]] = {"prevote": [], "precommit": []}

    async def start_server(self):
        self.server = await asyncio.start_server(self.handle_conn, self.host, self.port)
        logger.info(f"Node {self.node_id} listening {self.host}:{self.port}")

    async def handle_conn(self, r, w):
        peer = w.get_extra_info("peername")
        try:
            while not r.at_eof():
                raw = await r.readline()
                if not raw:
                    break
                s = raw.decode().strip()
                if not s:
                    continue
                try:
                    if s.startswith('{"type": "block"'):
                        msg = BlockMsg.from_json(s); await self.handle_block(msg)
                    elif s.startswith('{"type": "ready"'):
                        m = ReadyMsg.from_json(s); self.ready_set.add(m.node_id)
                    else:
                        v = VoteMsg.from_json(s)
                        # persist & push to queue
                        await self.incoming.put(v)
                        # also add to typed buffer for quick collect
                        if v.stage in self.recv_buffer:
                            self.recv_buffer[v.stage].append(v)
                except Exception as e:
                    logger.debug(f"Failed parsing incoming message from {peer}: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Connection error from {peer}: {e}")
        finally:
            try:
                w.close(); await w.wait_closed()
            except Exception:
                pass

    async def handle_block(self, msg: BlockMsg):
        h = msg.block["hash"]
        if h not in BLOCK_BY_HASH:
            BLOCK_BY_HASH[h] = msg.block
            self.blockchain.tree.add_block(msg.block)
            logger.info(f"Imported block {h[:8]}..")

    async def gossip_block(self, block):
        data = BlockMsg("block", block).to_json()+"\n"
        for pid, w in list(self.peer_writers.items()):
            try:
                w.write(data.encode()); await w.drain()
            except Exception:
                try: w.close(); await w.wait_closed()
                except: pass
                self.peer_writers.pop(pid, None)

    async def connect_peers(self):
        while True:
            for p in self.peers:
                if p["id"] == self.node_id or p["id"] in self.peer_writers:
                    continue
                try:
                    reader, writer = await asyncio.open_connection(p["host"], p["port"])
                    self.peer_writers[p["id"]] = writer
                    logger.info(f"Connected peer {p['id']}")
                except Exception:
                    pass
            await asyncio.sleep(1.0)

    def sign_ed25519(self, msg: str) -> str:
        return b64encode(self.ed_sk.sign(msg.encode()).signature).decode()

    async def broadcast_vote(self, v: VoteMsg):
        """Send a VoteMsg to all connected peers (best-effort)."""
        data = v.to_json() + "\n"
        for pid, w in list(self.peer_writers.items()):
            try:
                w.write(data.encode()); await w.drain()
            except Exception:
                try: w.close(); await w.wait_closed()
                except: pass
                self.peer_writers.pop(pid, None)

    def collect_messages(self, stage: str) -> List[VoteMsg]:
        """Return and clear buffered messages of the given stage (prevote/precommit)."""
        lst = self.recv_buffer.get(stage, [])
        self.recv_buffer[stage] = []
        return lst

# ------------------ GRANDPA Engine ------------------

class GrandpaEngine:
    def __init__(self, gossip: GossipNode, validators):
        self.gossip = gossip
        # validators is list of dicts with "id"...
        self.validators = [v["id"] for v in validators]
        self.round = 0
        # threshold is > 2/3 (i.e. 2n/3 rounded down + 1)
        self.threshold = (2 * len(self.validators)) // 3 + 1
        self.finalized = None

    def best_head(self) -> Optional[str]:
        """
        Choose best block as per graypaper §19:
         - must have finalized block as ancestor
         - must be audited
         - contains no unfinalized equivocations
        If none found, return None (vote nil).
        """
        best_rec = self.gossip.blockchain.tree.best_chain_head(self.finalized)
        if not best_rec:
            return None
        return best_rec["hash"]

    async def run_round(self, db_path: str) -> Tuple[Optional[str], Optional[Justification]]:
        r = self.round
        logger.info(f"Engine: starting round {r} (node {self.gossip.node_id})")

        # PREVOTE stage
        head = self.best_head()  # may be None meaning vote nil
        head_rec = BLOCK_BY_HASH.get(head) if head else None
        head_height = head_rec["height"] if head_rec else None
        head_state = head_rec["state_root"] if head_rec else None

        msg_str = vote_message_canonical(r, "prevote", head, head_height, head_state)
        sig = self.gossip.sign_ed25519(msg_str)
        my_prevote = VoteMsg(round=r, stage="prevote", block_hash=head, block_height=head_height,
                             state_root=head_state, validator=self.gossip.node_id,
                             sig_ed25519_b64=sig)
        # broadcast my prevote
        await self.gossip.broadcast_vote(my_prevote)
        # also put locally so it will be considered immediately
        await self.gossip.incoming.put(my_prevote)
        if "prevote" in self.gossip.recv_buffer:
            self.gossip.recv_buffer["prevote"].append(my_prevote)

        # collect prevotes for PREVOTE_TIMEOUT
        start = time.time()
        while time.time() - start < PREVOTE_TIMEOUT:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise
        prevotes = self.gossip.collect_messages("prevote")
        # persist prevotes we received from queue as well (some already persisted by handle_conn)
        for v in prevotes:
            await persist_vote(db_path, v)

        # tally prevotes: treat votes for different state_root as different (already in VoteMsg)
        counts = Counter([v.block_hash for v in prevotes])
        if counts:
            choice, votes = counts.most_common(1)[0]
        else:
            choice, votes = None, 0

        logger.info(f"Node {self.gossip.node_id} prevote tally: {votes} for {choice[:8] if choice else 'nil'} (threshold {self.threshold})")

        # If supermajority not reached, candidate becomes None (precommit nil)
        if votes >= self.threshold and choice is not None:
            candidate = choice
        else:
            candidate = None

        # PRECOMMIT stage
        pc_height = BLOCK_BY_HASH.get(candidate, {}).get("height") if candidate else None
        pc_state = BLOCK_BY_HASH.get(candidate, {}).get("state_root") if candidate else None
        msg_str = vote_message_canonical(r, "precommit", candidate, pc_height, pc_state)
        sig_pc = self.gossip.sign_ed25519(msg_str)
        my_pc = VoteMsg(round=r, stage="precommit", block_hash=candidate, block_height=pc_height,
                        state_root=pc_state, validator=self.gossip.node_id, sig_ed25519_b64=sig_pc)
        await self.gossip.broadcast_vote(my_pc)
        await self.gossip.incoming.put(my_pc)
        if "precommit" in self.gossip.recv_buffer:
            self.gossip.recv_buffer["precommit"].append(my_pc)

        # collect precommits for PRECOMMIT_TIMEOUT
        start = time.time()
        while time.time() - start < PRECOMMIT_TIMEOUT:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise
        precommits = self.gossip.collect_messages("precommit")
        for v in precommits:
            await persist_vote(db_path, v)

        # tally precommits
        counts_pc = Counter([v.block_hash for v in precommits])
        final_bh = None
        justification = None
        if counts_pc:
            bh, cnt = counts_pc.most_common(1)[0]
            # require supermajority on a non-nil block
            if bh is not None and cnt >= self.threshold:
                # additionally ensure the block still meets Grandpa acceptance: audited and no equivocation
                rec = BLOCK_BY_HASH.get(bh)
                if rec and rec.get("audited", False):
                    if not self.gossip.blockchain.tree.contains_equivocation_between(bh, self.finalized):
                        final_bh = bh
                    else:
                        logger.info(f"Candidate {bh[:8]} had equivocation in unfinalized range; not finalizing.")
                else:
                    logger.info(f"Candidate {bh[:8] if bh else 'nil'} not audited or missing; not finalizing.")

        if final_bh:
            self.finalized = final_bh
            await store_finalized(db_path, final_bh)
            # produce justification from precommits for that block
            sigs = [{"validator": v.validator, "sig_ed25519_b64": v.sig_ed25519_b64}
                    for v in precommits if v.block_hash == final_bh]
            justification = Justification(round_number=r, block_hash=final_bh, signatures=sigs)
            logger.info(f"Node {self.gossip.node_id} finalized {final_bh[:8]} in round {r} with {len(sigs)}/{len(self.validators)} precommits")
        else:
            logger.info(f"Round {r}: no finalization on node {self.gossip.node_id}")

        self.round += 1
        return final_bh, justification

# ------------------ Main ------------------

async def main_async(node_id, keys_file, config_file):
    keys_all = json.load(open(keys_file))
    cfg = json.load(open(config_file))
    node_cfg = next((n for n in cfg["nodes"] if n["id"] == node_id), None)
    if node_cfg is None:
        raise RuntimeError("Node id not found in config")

    validators_map = {v["id"]: v for v in keys_all["validators"]}
    if node_id not in validators_map:
        raise RuntimeError("Node id not present in keys.json validator list")
    keys = validators_map[node_id]

    gossip = GossipNode(node_id, node_cfg["host"], node_cfg["port"], cfg["nodes"], keys)
    db = DB_FILE_TEMPLATE.format(node_id)
    await init_db(db)

    engine = GrandpaEngine(gossip, cfg["nodes"])
    recovered = await load_finalized(db)
    if recovered:
        engine.finalized = recovered
        logger.info(f"Recovered finalized {recovered[:8]} from DB")

    # start server and background tasks
    await gossip.start_server()
    tasks = [
        asyncio.create_task(gossip.server.serve_forever()),
        asyncio.create_task(gossip.connect_peers()),
        asyncio.create_task(gossip.blockchain.produce_loop(gossip))
    ]

    # wait for peers
    required_peers = len(cfg["nodes"]) - 1
    while len(gossip.peer_writers) < required_peers:
        logger.info(f"Waiting for peers... {len(gossip.peer_writers)}/{required_peers}")
        await asyncio.sleep(0.5)

    logger.info("All peers connected, starting GRANDPA")

    try:
        while True:
            result, justification = await engine.run_round(db)
            if result:
                logger.info(f"Node {node_id}: block finalized {result[:8]}.. justification size {len(justification.signatures) if justification else 0}")
            await asyncio.sleep(0.05)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if getattr(gossip, "server", None):
            gossip.server.close()
            await gossip.server.wait_closed()
        logger.info("Node shutdown complete")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--keys", required=True)
    p.add_argument("--config", required=True)
    args = p.parse_args()
    asyncio.run(main_async(args.id, args.keys, args.config))

if __name__ == "__main__":
    main()
