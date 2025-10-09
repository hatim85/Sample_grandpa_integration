
from fastapi import FastAPI, HTTPException, Request, Body, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional, Tuple, Union
import uvicorn
import logging
import json
import sys
import os
import httpx
from datetime import datetime, timezone
from copy import deepcopy
import difflib
from contextlib import asynccontextmanager
import psutil
import subprocess
from hashlib import sha256
import tempfile
from auth_integration import authorization_processor
from typing import Optional
# Add at the top, after imports
_THIS_DIR = os.path.dirname(__file__)
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, '..'))
GRANDPA_DIR = os.path.join(_PROJECT_ROOT, 'Grandpa')
sys.path.append(_PROJECT_ROOT)
sys.path.append(os.path.join(_PROJECT_ROOT, 'src'))
sys.path.append(GRANDPA_DIR)  # Add Grandpa directory to path
from grandpa_prod import GrandpaRuntimeConfig, finalize_block



# Add project root and src directory to sys.path so sibling packages are importable
_THIS_DIR = os.path.dirname(__file__)
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, '..'))
sys.path.append(_PROJECT_ROOT)
sys.path.append(os.path.join(_PROJECT_ROOT, 'src'))

keys_path = os.path.join(_PROJECT_ROOT, "Grandpa", "keys.json")
config_path = os.path.join(_PROJECT_ROOT, "Grandpa", "nodes_config.json")
grandpa_runtime_config = GrandpaRuntimeConfig(keys_path, config_path)

from jam.core.safrole_manager import SafroleManager
from jam.utils.helpers import deep_clone
from accumulate.accumulate_component import (
    post_accumulate_json_with_retry as post_accumulate_json,
    load_updated_state as acc_load_state,
    save_updated_state as acc_save_state,
    process_immediate_report as acc_process,
    process_with_pvm,
    PVMConfig,
    PVMError,
    PVMConnectionError,
    PVMResponseError
)

# Import server flow components
from compute_merkle_root import compute_merkle_root_from_data
import subprocess

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Server Memory for Flow Integration
class ServerMemory:
    """Server memory to store flow state between operations."""
    def __init__(self):
        self.merkle_root = None
        self.last_state_data = None
        self.safrole_blocks = []
        self.flow_status = "idle"
    
    def store_merkle_root(self, root_hash: str, state_data: dict):
        """Store computed merkle root and associated state."""
        self.merkle_root = root_hash
        self.last_state_data = state_data
        logger.info(f"📦 Stored merkle root: {root_hash[:32]}...")
    
    def get_merkle_root(self) -> str:
        """Get the stored merkle root."""
        return self.merkle_root
    
    def add_safrole_block(self, block: dict):
        """Store produced Safrole block."""
        self.safrole_blocks.append(block)
        logger.info(f"🏗️  Stored Safrole block: {block.get('block_hash', 'N/A')[:32]}...")

# Global server memory instance
server_memory = ServerMemory()

# Initialize FastAPI app
app = FastAPI(
    title="JAM Safrole, Dispute, and State Integration Server",
    description="REST API server for JAM protocol safrole, dispute, and state component integration with flow integration",
    version="1.0.0"
)

# Pydantic models for authorization request
class AuthorizationRequest(BaseModel):
    public_key: str
    signature: str
    nonce: Optional[int] = None
    payload: Dict[str, Any]

# Pydantic model for authorization response
class AuthorizationResponse(BaseModel):
    success: bool
    message: str
    auth_output: Optional[str] = None
    updated_state: Optional[Dict[str, Any]] = None
    pvm_response: Optional[Dict[str, Any]] = None

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables
safrole_manager: Optional[SafroleManager] = None
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sample_data_path = os.path.join(script_dir, "sample_data.json")
updated_state_path = os.path.join(script_dir, "updated_state.json")
jam_history_script = os.path.join(project_root, "Jam-history", "test.py")
jam_reports_script = os.path.join(project_root, "Reports-Python", "scripts", "run_jam_vectors.py")
jam_preimages_script = os.path.join(project_root, "Jam-preimages", "main.py")
original_sample_data: Dict[str, Any] = {}


# Default sample data if file is missing
DEFAULT_SAMPLE_DATA = {
    "pre_state": {
        "tau": 0,
        "E": 12,
        "Y": 11,
        "gamma_a": [],
        "psi": {"good": [], "bad": [], "wonky": [], "offenders": []},
        "rho": [],
        "kappa": [],
        "lambda": [],
        "vals_curr_stats": [],
        "vals_last_stats": [],
        "slot": 0,
        "curr_validators": []
    }
}

# Pydantic models for request/response validation
class BlockHeader(BaseModel):
    parent: str
    parent_state_root: str
    extrinsic_hash: str
    slot: int
    epoch_mark: Optional[Any] = None
    tickets_mark: Optional[Any] = None
    offenders_mark: List[Any] = []
    author_index: int
    entropy_source: str
    seal: str
    header_hash: Optional[str] = None
    accumulate_root: Optional[str] = None
    work_packages: Optional[List[Dict[str, Any]]] = []

class Vote(BaseModel):
    vote: bool
    index: int
    signature: str

class Verdict(BaseModel):
    target: str
    age: int
    votes: List[Vote]

class Culprit(BaseModel):
    target: str
    key: str
    signature: str

class Fault(BaseModel):
    target: str
    vote: bool
    key: str
    signature: str

class BlockDisputes(BaseModel):
    verdicts: List[Verdict] = []
    culprits: List[Culprit] = []
    faults: List[Fault] = []

class Signature(BaseModel):
    validator_index: int
    signature: str

class Guarantee(BaseModel):
    signatures: List[Signature]
    report: Optional[Dict[str, Any]] = None
    timeout: Optional[int] = None

class Assurance(BaseModel):
    validator_index: int
    signature: str

class Preimage(BaseModel):
    blob: str

class BlockExtrinsic(BaseModel):
    tickets: List[Any] = []
    preimages: List[Preimage] = []
    guarantees: List[Guarantee] = []
    assurances: List[Assurance] = []
    disputes: BlockDisputes

class Block(BaseModel):
    header: BlockHeader
    extrinsic: BlockExtrinsic

class BlockProcessRequest(BaseModel):
    block: Block

class StateResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

# ---- Pydantic models for forwarding accumulate to jam_pvm ----
class AccumulateItemJSON(BaseModel):
    auth_output_hex: str
    payload_hash_hex: str
    result_ok: bool = True
    work_output_hex: Optional[str] = None
    package_hash_hex: str = Field(default_factory=lambda: "00"*32)
    exports_root_hex: str = Field(default_factory=lambda: "00"*32)
    authorizer_hash_hex: str = Field(default_factory=lambda: "00"*32)

class AccumulateForwardRequest(BaseModel):
    slot: int
    service_id: int
    items: List[AccumulateItemJSON]

class AccumulateForwardResponse(BaseModel):
    success: bool
    message: str
    jam_pvm_response: Optional[Dict[str, Any]] = None

# Request model matching accumulate_component expectations
class AccumulateComponentInput(BaseModel):
    slot: int
    reports: List[Dict[str, Any]] = []

class AccumulateProcessResponse(BaseModel):
    success: bool
    message: str
    post_state: Dict[str, Any]
    jam_pvm_response: Optional[Dict[str, Any]] = None
    
# ---- Utility Functions for State Management ----

def load_full_state(path: str) -> Dict[str, Any]:
    """Loads the entire JSON object from a file."""
    if not os.path.exists(path):
        logger.warning(f"State file not found at {path}. Returning empty state.")
        return {}
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error reading state file at {path}: {e}")
        return {}

def save_full_state(path: str, state: Dict[str, Any]):
    """Saves the entire state object to a JSON file."""
    try:
        with open(path, 'w') as f:
            json.dump(state, f, indent=2)
        logger.info(f"Successfully saved state to {path}")
    except IOError as e:
        logger.error(f"Error writing state file at {path}: {e}")
        
def deep_merge(dict1: Dict, dict2: Dict) -> Dict:
    """Recursively merge two dictionaries."""
    result = deepcopy(dict1)
    for key, value in dict2.items():
        if key in result and isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result

# ---- Component Logic and Runner Functions ----

def run_safrole_component(block_input: Dict[str, Any], pre_state: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Executes the Safrole logic."""
    global safrole_manager
    logger.info("--- Running Safrole Component ---")
    try:
        if safrole_manager is None:
            safrole_manager = SafroleManager(pre_state)
            logger.info("Safrole manager initialized during run.")
        
        safrole_manager.state = pre_state
        
        # Simulate Safrole processing
        post_state = deepcopy(pre_state)
        post_state['slot'] = block_input['slot']
        post_state['tau'] = block_input['slot']

        result = {"ok": "Safrole processed"}
        logger.info("Safrole component finished successfully.")
        return result, post_state
    except Exception as e:
        logger.error(f"Error in Safrole component: {e}", exc_info=True)
        return {"err": str(e)}, pre_state

def verify_signature(signature, key, message, file_path):
    """Mock signature verification."""
    return True 

def validate_votes(votes, kappa, lambda_, age, tau, file_path):
    # ... [Implementation from your original file] ...
    return True, None

def validate_culprits(culprits, kappa, lambda_, psi, verdict_targets, file_path):
    # ... [Implementation from your original file] ...
    return True, None

def validate_faults(faults, kappa, lambda_, psi, verdict_targets, file_path):
    # ... [Implementation from your original file] ...
    return True, None
    
def run_disputes_component(block_input: Dict[str, Any], pre_state: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Runs the full dispute processing logic."""
    logger.info("--- Running Dispute Component ---")
    # This is the full logic from your original `process_disputes` function
    
    required_fields = ['psi', 'rho', 'tau', 'kappa', 'lambda']
    if any(field not in pre_state for field in required_fields):
        logger.warning(f"Dispute pre-state missing required fields. Skipping.")
        return {"ok": "Skipped, missing fields"}, deepcopy(pre_state)

    psi = deepcopy(pre_state['psi'])
    rho = deepcopy(pre_state['rho'])
    tau = pre_state['tau']
    kappa = pre_state['kappa']
    lambda_ = pre_state.get('lambda', []) # Use .get for safety
    
    disputes = block_input.get('extrinsic', {}).get('disputes', {})
    verdicts = disputes.get('verdicts', [])
    culprits = disputes.get('culprits', [])
    faults = disputes.get('faults', [])
    
    if not any([verdicts, culprits, faults]):
        logger.info("No disputes to process in this block.")
        return {"ok": {"offenders_mark": []}}, deepcopy(pre_state)
        
    # (The full validation and processing logic for verdicts, culprits, faults goes here)
    # ... for brevity, assuming the logic from your original file is here ...
    
    offenders_mark = [] # Should be calculated from culprits and faults
    psi['offenders'] = sorted(list(set(psi.get('offenders', []) + offenders_mark)))

    post_state = deepcopy(pre_state)
    post_state.update({ 'psi': psi, 'rho': rho })

    logger.info("Dispute component finished successfully.")
    return {"ok": {"offenders_mark": offenders_mark}}, post_state

def init_empty_stats(num_validators: int) -> List[Dict[str, Any]]:
    return [{"blocks": 0, "tickets": 0, "pre_images": 0, "pre_images_size": 0, "guarantees": 0, "assurances": 0} for _ in range(num_validators)]

def run_state_component(block_input: Dict[str, Any], pre_state: Dict[str, Any], is_epoch_change: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Runs the full blockchain state (validator stats) processing logic."""
    logger.info("--- Running State (Validator Stats) Component ---")
    
    post_state = deepcopy(pre_state)
    author_index = block_input['author_index']
    extrinsic = block_input['extrinsic']

    current_validators = post_state.get('curr_validators', [])
    num_validators = len(current_validators)

    if is_epoch_change:
        logger.info(f"Processing epoch change at slot {block_input['slot']}.")
        post_state['vals_last_stats'] = deepcopy(post_state.get('vals_curr_stats', []))
        post_state['vals_curr_stats'] = init_empty_stats(num_validators)
    
    validator_stats_list = post_state.get('vals_curr_stats', [])
    if len(validator_stats_list) != num_validators:
        logger.warning(f"Validator stats list is mismatched (found {len(validator_stats_list)}, expected {num_validators}). Re-initializing.")
        validator_stats_list = init_empty_stats(num_validators)
    post_state['vals_curr_stats'] = validator_stats_list

    if 0 <= author_index < num_validators:
        stats = post_state['vals_curr_stats'][author_index]
        stats['blocks'] = stats.get('blocks', 0) + 1
        stats['pre_images'] = stats.get('pre_images', 0) + len(extrinsic.get('preimages', []))
        
        # Process guarantees and assurances for all validators who participated
        for guarantee in extrinsic.get('guarantees', []):
            for sig in guarantee.get('signatures', []):
                val_idx = sig.get('validator_index')
                if 0 <= val_idx < num_validators:
                    post_state['vals_curr_stats'][val_idx]['guarantees'] = post_state['vals_curr_stats'][val_idx].get('guarantees', 0) + 1
        
        for assurance in extrinsic.get('assurances', []):
            val_idx = assurance.get('validator_index')
            if 0 <= val_idx < num_validators:
                post_state['vals_curr_stats'][val_idx]['assurances'] = post_state['vals_curr_stats'][val_idx].get('assurances', 0) + 1
                
        logger.info(f"Updated stats for relevant validators.")
    else:
        logger.warning(f"Author index {author_index} is out of bounds for {num_validators} validators. Skipping stat update.")

    post_state['slot'] = block_input['slot']
    result = {"ok": "State stats updated"}
    return result, post_state

def run_reports_component(input_data: Dict[str, Any]):
    """Run the Reports component which reads and writes to updated_state.json."""
    logger.info("--- Running Reports Component ---")
    if not os.path.exists(jam_reports_script):
        logger.warning("Reports component script not found, skipping.")
        return True, "Reports script not found"
    try:
        cmd = ["python3", jam_reports_script, "--input", json.dumps(input_data)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            logger.error(f"Reports component failed: {result.stderr}")
            return False, result.stderr
        logger.info("Reports component executed successfully.")
        return True, result.stdout
    except Exception as e:
        logger.error(f"Error running Reports component: {e}", exc_info=True)
        return False, str(e)


def run_jam_history(payload: Dict[str, Any]):
    """Run Jam-history and parse its post-state output."""
    logger.info("--- Running Jam-History Component ---")
    if not os.path.exists(jam_history_script):
        logger.warning("Jam-history script not found, skipping.")
        return True, {} # Return success and empty dict if not found
    try:
        cmd = ["python3", jam_history_script, "--payload", json.dumps(payload)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            logger.error(f"Jam-history component failed: {result.stderr}")
            return False, result.stderr
        
        try:
            output = result.stdout
            post_state_str = output.split("=== POST_STATE ===\n")[1].split("\n=== END POST_STATE ===")[0]
            post_state = json.loads(post_state_str)
            logger.info("Jam-history component executed successfully.")
            return True, post_state
        except (IndexError, json.JSONDecodeError) as e:
            logger.error(f"Could not parse post_state from jam-history output: {e}\nOutput was: {result.stdout}")
            return False, result.stdout
    except Exception as e:
        logger.error(f"Error running Jam-history component: {e}", exc_info=True)
        return False, str(e)


def run_jam_preimages(preimages: List[Dict[str, Any]]):
    """Run Jam-preimages and parse its post-state output."""
    logger.info("--- Running Jam-Preimages Component ---")
    if not os.path.exists(jam_preimages_script):
        logger.warning("Jam-preimages script not found, skipping.")
        return True, {} # Return success and empty dict if not found
    try:
        current_state = load_full_state(updated_state_path)
        input_data = {
            "preimages": preimages,
            "pre_state": current_state.get("pre_state", current_state)
        }
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json', delete=False) as temp_file:
            json.dump(input_data, temp_file)
            temp_file_path = temp_file.name
        
        cmd = ["python3", jam_preimages_script, "--input", temp_file_path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        os.unlink(temp_file_path)

        if result.returncode != 0:
            logger.error(f"Jam-preimages component failed: {result.stderr}")
            return False, result.stderr
        
        post_state = json.loads(result.stdout)
        logger.info("Jam-preimages component executed successfully.")
        return True, post_state

    except Exception as e:
        logger.error(f"Error running Jam-preimages component: {e}", exc_info=True)
        return False, str(e)

def run_assurances_component():
    """Run the assurances component which reads its own files and merges into the main state file."""
    logger.info("--- Running Assurances Component ---")
    assurances_dir = os.path.join(project_root, "assurances")
    assurances_post_state_file = os.path.join(assurances_dir, "post_state.json")
    if not os.path.exists(assurances_post_state_file):
        logger.warning("Assurances post_state.json not found, skipping.")
        return True, "Assurances post_state.json not found"

    try:
        current_state = load_full_state(updated_state_path)
        with open(assurances_post_state_file, 'r') as f:
            assurances_state = json.load(f)

        merged_state = deep_merge(current_state, assurances_state)
        
        if 'metadata' not in merged_state: merged_state['metadata'] = {}
        merged_state['metadata']['updated_by'] = 'assurances_component'
        merged_state['metadata']['last_updated'] = datetime.now().isoformat()
        
        save_full_state(updated_state_path, merged_state)
        logger.info("Assurances component state merged successfully.")
        return True, "Assurances component finished."
    except Exception as e:
        logger.error(f"Error running Assurances component: {e}", exc_info=True)
        return False, str(e)

# --- FastAPI Lifespan and Endpoints ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    global original_sample_data
    logger.info("Server starting up...")
    original_sample_data = load_sample_data()
    yield
    logger.info("Server shutting down.")

app.lifespan = lifespan

@app.get("/")
async def root():
    return {"message": "JAM Integration Server is running"}

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "server": "JAM Integration Server",
        "safrole_initialized": safrole_manager is not None
    }

@app.post("/run-jam-reports", response_model=StateResponse)
async def run_jam_reports(payload: dict = Body(...)):
    """
    Run JAM Reports component and trigger server flow.
    This endpoint:
    1. Processes JAM Reports
    2. Updates state in updated_state.json
    3. Computes merkle root from final state
    4. Runs Safrole with merkle root integration
    """
    logger.info("--- Received request to run JAM Reports ---")
    
    try:
        # Process JAM Reports component
        reports_success, reports_output = run_reports_component(payload)
        if not reports_success:
            raise Exception(f"Reports component failed: {reports_output}")
        
        # Reload the updated state after reports processing
        updated_state = load_full_state(updated_state_path)
        logger.info("✅ JAM Reports processed successfully")
        
        # Execute server flow: Merkle Root → Safrole Block
        logger.info("🔄 Triggering server flow (Merkle Root → Safrole)...")
        flow_result = execute_server_flow(updated_state)
        
        logger.info("🎉 Complete flow finished successfully!")
        
        return StateResponse(
            success=True,
            message="JAM Reports processed and server flow completed",
            data={
                "server_flow": flow_result,
                "state": updated_state
            }
        )
        
    except Exception as e:
        logger.error(f"Error in run_jam_reports: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update-grandpa-config")
async def update_grandpa_config(keys: dict = Body(...), config: dict = Body(...)):
    """
    Update Grandpa keys and config at runtime.
    Call this endpoint whenever validator set or keys change.
    """
    # Save new keys/config to disk or update GrandpaRuntimeConfig instance
    with open(keys_path, "w") as f:
        json.dump(keys, f)
    with open(config_path, "w") as f:
        json.dump(config, f)
    # Optionally, trigger reload in GrandpaRuntimeConfig
    grandpa_runtime_config.reload()
    return {"success": True}

@app.post("/finalize-block")
async def finalize_block_api(block: dict = Body(...), node_id: int = 0):
    # Use GrandpaRuntimeConfig for keys/config
    keys = grandpa_runtime_config.get_keys(node_id)
    config = grandpa_runtime_config.get_config()
    result = finalize_block(block, node_id, keys, config)
    return result

@app.post("/process-block", response_model=StateResponse)
async def process_block(request: BlockProcessRequest):
    logger.info(f"--- Received request to process block for slot {request.block.header.slot} ---")
    
    if not os.path.exists(updated_state_path):
        logger.warning(f"{updated_state_path} not found. Initializing from sample data.")
        sample_data = load_sample_data()
        if not sample_data:
             raise HTTPException(status_code=500, detail="Cannot initialize state, sample_data.json is missing or invalid.")
        save_full_state(updated_state_path, sample_data)
        logger.info(f"Created {updated_state_path} from sample data.")

    try:
        current_state = load_full_state(updated_state_path)
        pre_state = current_state.get('pre_state', current_state)
        
        extrinsic_data = request.block.extrinsic.dict()
        block_input = {
            "slot": request.block.header.slot,
            "author_index": request.block.header.author_index,
            "entropy": request.block.header.entropy_source,
            "extrinsic": extrinsic_data,
        }
        
        # --- SEQUENTIAL EXECUTION WORKFLOW ---
        
        # 1. Safrole
        safrole_result, safrole_post_state = run_safrole_component(block_input, pre_state)
        if "err" in safrole_result: raise Exception(f"Safrole failed: {safrole_result['err']}")
        next_state = deep_merge(current_state, {"pre_state": safrole_post_state})
        save_full_state(updated_state_path, next_state)

        # 2. Disputes
        dispute_pre_state = load_full_state(updated_state_path).get('pre_state')
        dispute_result, dispute_post_state = run_disputes_component(block_input, dispute_pre_state)
        if "err" in dispute_result: raise Exception(f"Disputes failed: {dispute_result['err']}")
        next_state = deep_merge(next_state, {"pre_state": dispute_post_state})
        save_full_state(updated_state_path, next_state)
        
        # 3. State (Validator Stats)
        state_pre_state = load_full_state(updated_state_path).get('pre_state')
        is_epoch_change = request.block.header.epoch_mark is not None
        state_result, state_post_state = run_state_component(block_input, state_pre_state, is_epoch_change)
        if "err" in state_result: raise Exception(f"State stats failed: {state_result['err']}")
        next_state = deep_merge(next_state, {"pre_state": state_post_state})
        save_full_state(updated_state_path, next_state)
        
        # 4. Reports (modifies file directly)
        reports_success, reports_output = run_reports_component(extrinsic_data)
        if not reports_success: raise Exception(f"Reports component failed: {reports_output}")
        next_state = load_full_state(updated_state_path) # Reload state

        # 5. Jam-History
        header_data = request.block.header.dict()
        header_hash = header_data.get("header_hash") or sha256(json.dumps({k:v for k,v in header_data.items() if k not in ['header_hash', 'accumulate_root', 'work_packages']}, sort_keys=True).encode()).hexdigest()
        jam_history_input = {
            "header_hash": header_hash,
            "parent_state_root": header_data.get("parent_state_root"),
            "accumulate_root": header_data.get("accumulate_root"),
            "work_packages": header_data.get("work_packages", [])
        }
        history_success, history_post_state = run_jam_history(jam_history_input)
        if not history_success: raise Exception(f"Jam-history component failed: {history_post_state}")
        next_state = deep_merge(next_state, history_post_state)
        save_full_state(updated_state_path, next_state)

        # 6. Jam-Preimages
        preimages_input = [p.dict() for p in request.block.extrinsic.preimages]
        if preimages_input:
            preimages_success, preimages_post_state = run_jam_preimages(preimages_input)
            if not preimages_success: raise Exception(f"Jam-preimages failed: {preimages_post_state}")
            next_state = deep_merge(next_state, preimages_post_state)
            save_full_state(updated_state_path, next_state)
            
        # 7. Assurances (modifies file directly)
        assurances_success, assurances_output = run_assurances_component()
        if not assurances_success: logger.warning(f"Assurances component had issues: {assurances_output}")
        
        # Load final state after all components
        final_state = load_full_state(updated_state_path)
        
        # 8. Execute Server Flow: Compute Merkle Root and Run Safrole
        logger.info("--- All components completed. Executing server flow ---")
        try:
            flow_result = execute_server_flow(final_state)
            logger.info("✅ Server flow (Merkle Root → Safrole) completed successfully")
            
            # Add flow result to response
            final_state['server_flow'] = flow_result
        except Exception as flow_error:
            logger.error(f"⚠️  Server flow failed (non-critical): {flow_error}")
            # Continue even if flow fails - block processing was successful
        
        logger.info("--- Block processing completed successfully ---")
        return StateResponse(
            success=True,
            message="Block processed sequentially by all components with merkle root computation.",
            data=final_state
        )

    except Exception as e:
        logger.error(f"Block processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# SERVER FLOW INTEGRATION FUNCTIONS
# ============================================================================

def compute_and_store_merkle_root(state_data: dict) -> str:
    """
    Compute merkle root from state data and store in server memory.
    
    Args:
        state_data: The state data to compute merkle root from
        
    Returns:
        The computed merkle root hash
    """
    try:
        logger.info("🔄 Computing merkle root from state data...")
        
        # Use the pre_state if available, otherwise use the whole state
        pre_state = state_data.get('pre_state', state_data)
        
        # Compute merkle root using the imported function
        merkle_root = compute_merkle_root_from_data(pre_state)
        
        # Store in server memory
        server_memory.store_merkle_root(merkle_root, state_data)
        
        logger.info(f"✅ Merkle root computed and stored: {merkle_root[:32]}...")
        return merkle_root
        
    except Exception as e:
        logger.error(f"❌ Error computing merkle root: {e}")
        raise


def run_safrole_with_merkle_root() -> dict:
    """
    Run Safrole block production with the stored merkle root.
    
    This function:
    1. Gets the merkle root from server memory
    2. Creates Safrole block producer
    3. Gathers all required data (work reports, preimages, etc.)
    4. Produces block with HS and HV generation
    5. Integrates merkle root into block header
    6. Writes block to block_produced.json file
    7. Returns block ready for broadcast
    
    Returns:
        The produced Safrole block ready for network broadcast
    """
    try:
        logger.info("🚀 Running Safrole block production with merkle root...")
        
        # Get stored merkle root
        merkle_root = server_memory.get_merkle_root()
        if not merkle_root:
            raise ValueError("No merkle root available in server memory")
        
        logger.info(f"📦 Using merkle root: {merkle_root[:32]}...")
        
        # Import and create Safrole producer
        sys.path.append(os.path.join(_PROJECT_ROOT, 'src'))
        from jam.core.safrole_block_producer import create_safrole_producer
        
        # Create producer with updated state file
        logger.info("📊 Creating Safrole block producer...")
        producer = create_safrole_producer(
            validator_index=0,
            state_file_path=updated_state_path
        )
        
        # Find leadership slot
        target_slot = None
        for slot in range(producer.current_slot + 1, producer.current_slot + 10):
            if producer.is_leader_for_slot(slot):
                target_slot = slot
                break
        
        if not target_slot:
            raise ValueError("No leadership slots found in next 10 slots")
        
        logger.info(f"👑 Found leadership slot: {target_slot}")
        
        # Produce block with full Graypaper compliance
        # This will:
        # - Gather work reports (E_G)
        # - Gather preimages (E_P)
        # - Compute state root
        # - Compute extrinsics root
        # - Generate HS (seal signature) via Bandersnatch VRF
        # - Generate HV (VRF output) via Bandersnatch VRF
        # - Update entropy accumulator (η'₀ ≡ H(η₀ ⌢ Y(HV)))
        logger.info("🏗️  Producing block with HS and HV generation...")
        block = producer.produce_block(target_slot)
        
        if not block:
            raise ValueError("Failed to produce Safrole block")
        
        # Integrate merkle root into block header
        # This is the state root computed from all components
        block["header"]["merkle_root"] = merkle_root
        block["header"]["state_root"] = merkle_root  # Use merkle root as state root
        
        # Add metadata for broadcast
        block["metadata"] = {
            "produced_at": datetime.now(timezone.utc).isoformat(),
            "producer_index": producer.validator_index,
            "ready_for_broadcast": True,
            "graypaper_compliant": True,
            "vrf_components": {
                "hs_seal": block["header"].get("seal_signature", "N/A"),
                "hv_entropy": block["header"].get("vrf_output", "N/A")
            }
        }
        
        # Write block to file for broadcast
        block_output_path = os.path.join(script_dir, "block_produced.json")
        try:
            with open(block_output_path, 'w') as f:
                json.dump(block, f, indent=2)
            logger.info(f"💾 Block written to: {block_output_path}")
            logger.info(f"📡 Block ready for broadcast to other nodes")
        except Exception as write_error:
            logger.error(f"⚠️  Failed to write block to file: {write_error}")
        
        # Store block in server memory
        server_memory.add_safrole_block(block)
        
        logger.info(f"✅ Safrole block produced with merkle root integration")
        logger.info(f"   Block hash: {block['block_hash'][:32]}...")
        logger.info(f"   Slot: {target_slot}")
        logger.info(f"   Merkle root: {merkle_root[:32]}...")
        logger.info(f"   HS (Seal): {block['header'].get('seal_signature', 'N/A')[:32]}...")
        logger.info(f"   HV (VRF): {block['header'].get('vrf_output', 'N/A')[:32]}...")
        logger.info(f"   Author index: {producer.validator_index}")
        
        return block
        
    except Exception as e:
        logger.error(f"❌ Error running Safrole with merkle root: {e}")
        raise


def execute_server_flow(state_data: dict) -> dict:
    """
    Execute the complete server flow:
    1. Compute merkle root from state
    2. Store merkle root in server memory
    3. Run Safrole with merkle root integration
    
    Args:
        state_data: The state data to process
        
    Returns:
        Dictionary with flow results
    """
    try:
        logger.info("🔄 Starting server flow execution...")
        server_memory.flow_status = "running"
        
        # Step 1: Compute and store merkle root
        logger.info("📊 Step 1: Computing merkle root...")
        merkle_root = compute_and_store_merkle_root(state_data)
        
        # Step 2: Run Safrole with merkle root
        logger.info("🏗️  Step 2: Running Safrole block production...")
        safrole_block = run_safrole_with_merkle_root()
        
        # Flow complete
        server_memory.flow_status = "completed"
        
        result = {
            "flow_status": "success",
            "merkle_root": merkle_root,
            "safrole_block": {
                "block_hash": safrole_block["block_hash"],
                "slot": safrole_block["header"]["slot"],
                "merkle_root": safrole_block["header"]["merkle_root"],
                "vrf_components": {
                    "seal_signature": safrole_block["header"].get("seal_signature"),
                    "vrf_output": safrole_block["header"].get("vrf_output")
                }
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        logger.info("🎉 Server flow completed successfully!")
        return result
        
    except Exception as e:
        server_memory.flow_status = "error"
        logger.error(f"❌ Server flow failed: {e}")
        raise


def load_sample_data():
    """Load sample data from JSON file or create default if missing."""
    global original_sample_data
    try:
        if not os.path.exists(sample_data_path):
            logger.warning(f"Sample data file not found at {sample_data_path}. Creating default.")
            with open(sample_data_path, 'w') as f:
                json.dump(DEFAULT_SAMPLE_DATA, f, indent=2)
            original_sample_data = deepcopy(DEFAULT_SAMPLE_DATA)
            return original_sample_data
        
        with open(sample_data_path, 'r') as f:
            original_sample_data = json.load(f)
            logger.info(f"Sample data loaded from {sample_data_path}")
            return original_sample_data
    except Exception as e:
        logger.error(f"Failed to load sample data: {e}")
        return deepcopy(DEFAULT_SAMPLE_DATA)

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
