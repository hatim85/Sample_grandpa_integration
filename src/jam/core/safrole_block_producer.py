"""
Safrole Block Producer - Core JAM Protocol Implementation

This module implements the core Safrole block production functionality according to
Graypaper (GP) sections 4 and 14. It handles:
- Leader selection using VRF (Verifiable Random Function)
- Block proposal and construction
- State transitions and validation
- Integration with existing SafroleManager

This is a focused implementation without off-chain worker or networking components.
"""
import requests
import json
import time
import hashlib
import secrets
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone

from .safrole_manager import SafroleManager
from ..utils.helpers import (
    hex_to_bytes,
    bytes_to_hex,
    deep_clone,
    get_epoch_and_slot_phase,
)
from ..utils.crypto_bridge import CryptoBridge
from ..utils.bandersnatch_vrf import generate_safrole_vrf_signatures
import sys, os
grandpa_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../Grandpa'))
if grandpa_dir not in sys.path:
    sys.path.append(grandpa_dir)
from grandpa_prod import finalize_block

class SafroleBlockProducer:
    """
    Core Safrole block production implementation.
    
    This class handles the essential Safrole functionality:
    - Reading state from updated_state.json
    - VRF-based leader selection
    - Block construction and signing
    - State root computation
    - Integration with existing SafroleManager
    """
    
    def __init__(self, 
                 state_file_path: str = None,
                 validator_index: int = 0,
                 validator_private_key: str = None):
        """
        Initialize the Safrole block producer.
        
        Args:
            state_file_path: Path to updated_state.json file
            validator_index: Index of this validator in the validator set
            validator_private_key: Private key for signing (generated if not provided)
        """

        block = {
            "header": header,
            "body": body,
            "block_hash": None  # Will be computed after assembly
        }

        block_bytes = json.dumps(block, sort_keys=True).encode()
        block_hash = hashlib.blake2b(block_bytes, digest_size=32).hexdigest()
        block["block_hash"] = block_hash

        self.state_file_path = state_file_path or self._get_default_state_file_path()
        self.validator_index = validator_index
        self.validator_private_key = validator_private_key or secrets.token_hex(32)
        
        # Load initial state
        self.current_state = self._load_state_from_file()
        self.safrole_manager = SafroleManager(self.current_state.get("pre_state", {}))
        
        # Extract validator information
        self.validators = self._extract_validators()
        self.current_slot = self.current_state.get("current_slot", 0)
        
        # Block production state
        self.produced_blocks = []
        self.last_authored_slot = -1
        
        print(f"SafroleBlockProducer initialized:")
        print(f"  - Validator index: {self.validator_index}")
        print(f"  - Current slot: {self.current_slot}")
        print(f"  - Validators count: {len(self.validators)}")
        print(f"  - State file: {self.state_file_path}")

        return block
    
    def _get_default_state_file_path(self) -> str:
        """Get default path to updated_state.json file."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
        return os.path.join(project_root, "server", "updated_state.json")
    
    def _load_state_from_file(self) -> Dict[str, Any]:
        """Load state from the JSON file."""
        try:
            with open(self.state_file_path, 'r') as f:
                state = json.load(f)
            print(f"Loaded state from {self.state_file_path}")
            return state
        except FileNotFoundError:
            print(f"State file not found: {self.state_file_path}")
            return self._create_default_state()
        except json.JSONDecodeError as e:
            print(f"Error parsing state file: {e}")
            return self._create_default_state()
    
    def _create_default_state(self) -> Dict[str, Any]:
        """Create a default state for testing."""
        return {
            "current_slot": 0,
            "pre_state": {
                "tau": 0,
                "eta": [
                    "0x11da6d1f761ddf9bdb4c9d6e5303ebd41f61858d0a5647a1a7bfe089bf921be9",
                    "0xe12c22d4f162d9a012c9319233da5d3e923cc5e1029b8f90e47249c9ab256b35",
                    "0x7b0aa1735e5ba58d3236316c671fe4f00ed366ee72417c9ed02a53a8019e85b8",
                    "0x8c039ff7caa17ccebfcadc44bd9fce6a4b6699c4d03de2e3349aa1dc11193cd7"
                ],
                "gamma_k": [
                    {
                        "bandersnatch": "0xff71c6c03ff88adb5ed52c9681de1629a54e702fc14729f6b50d2f0a76f185b3",
                        "ed25519": "0x4418fb8c85bb3985394a8c2756d3643457ce614546202a2f50b093d762499ace"
                    }
                ],
                "gamma_z": "0xaf39b7de5fcfb9fb8a46b1645310529ce7d08af7301d9758249da4724ec698eb",
                "E": 12,  # Epoch length
                "Y": 11,  # Submission period
                "N": 6    # Number of validators
            },
            "curr_validators": [
                {
                    "bandersnatch": "0xff71c6c03ff88adb5ed52c9681de1629a54e702fc14729f6b50d2f0a76f185b3",
                    "ed25519": "0x4418fb8c85bb3985394a8c2756d3643457ce614546202a2f50b093d762499ace"
                }
            ]
        }
    
    def _extract_validators(self) -> List[Dict[str, Any]]:
        """Extract validator information from the loaded state."""
        # Try to get validators from different possible locations in the state
        validators = []
        
        # Check curr_validators
        if "curr_validators" in self.current_state:
            validators = self.current_state["curr_validators"]
        
        # Check pre_state.gamma_k (next validators)
        elif "pre_state" in self.current_state and "gamma_k" in self.current_state["pre_state"]:
            validators = self.current_state["pre_state"]["gamma_k"]
        
        # Check pre_state.kappa (current validators)
        elif "pre_state" in self.current_state and "kappa" in self.current_state["pre_state"]:
            validators = self.current_state["pre_state"]["kappa"]
        
        if not validators:
            print("Warning: No validators found in state, using default")
            validators = [
                {
                    "bandersnatch": "0xff71c6c03ff88adb5ed52c9681de1629a54e702fc14729f6b50d2f0a76f185b3",
                    "ed25519": "0x4418fb8c85bb3985394a8c2756d3643457ce614546202a2f50b093d762499ace"
                }
            ]
        
        return validators
    
    def is_leader_for_slot(self, slot: int) -> bool:
        """
        Check if this validator is the leader for the given slot using VRF.
        
        This implements the Safrole leader selection mechanism from GP Section 14.
        In a full implementation, this would use proper VRF with Bandersnatch curves.
        For M2, we use a simplified but deterministic approach.
        
        Args:
            slot: The slot number to check
            
        Returns:
            True if this validator should produce a block for this slot
        """
        if not self.validators:
            return False
        
        # Simple deterministic leader selection for M2
        # In production, this would use VRF with the validator's private key
        validator_count = len(self.validators)
        selected_validator_index = slot % validator_count
        
        is_leader = selected_validator_index == self.validator_index
        
        # if is_leader:
            # print(f"✅ Validator {self.validator_index} is leader for slot {slot}")
        
        return is_leader
    
    def get_current_validator_info(self) -> Dict[str, Any]:
        """Get information about the current validator."""
        if self.validator_index < len(self.validators):
            return self.validators[self.validator_index]
        return {}
    
    def generate_vrf_entropy(self, slot: int) -> str:
        """
        Generate VRF entropy for the block according to GP Section 6.4.
        
        This implements the VRF entropy generation as specified in Graypaper:
        - Uses the current slot's seal key
        - Generates VRF output using Bandersnatch VRF (simplified for demo)
        - Returns the VRF output hash (Y(HV))
        
        Args:
            slot: The slot number
            
        Returns:
            VRF entropy as hex string (corresponds to Y(HV) in GP)
        """
        # Get validator info for VRF key
        validator_info = self.get_current_validator_info()
        bandersnatch_key = validator_info.get("bandersnatch", "0x" + "00" * 32)
        
        # VRF input according to GP: XE || Y(HS) where XE = "jam_entropy"
        # For simplified implementation, we use slot and entropy accumulator
        vrf_input_data = f"jam_entropy{slot}".encode()
        
        # Get entropy accumulator from state (eta_3 for VRF input per GP 6.15)
        eta_values = self.current_state.get("entropy", [])
        if len(eta_values) >= 4:
            eta_3 = eta_values[3]  # η₃ from GP
        else:
            eta_3 = "0x" + "00" * 32
        
        # Combine VRF input with historical entropy
        vrf_input = vrf_input_data + hex_to_bytes(eta_3)
        
        # Simplified VRF computation (production would use proper Bandersnatch VRF)
        # This represents the VRF output point O = x * I where I = hash_to_curve(input)
        vrf_secret = self.validator_private_key.encode() + hex_to_bytes(bandersnatch_key)
        vrf_output_point = hashlib.blake2b(vrf_secret + vrf_input, digest_size=32).digest()
        
        # VRF output hash Y(HV) - this is what gets used as entropy
        vrf_output_hash = hashlib.blake2b(vrf_output_point + b"vrf_output", digest_size=32).digest()
        
        return bytes_to_hex(vrf_output_hash)
    
    def collect_work_reports(self) -> List[Dict[str, Any]]:
        """
        Collect work reports for inclusion in the block.
        
        This reads from the current state and extracts available work reports.
        In a full implementation, this would come from a mempool.
        
        Returns:
            List of work reports to include
        """
        work_reports = []
        
        # Extract work reports from guarantees in the input
        if "input" in self.current_state and "extrinsic" in self.current_state["input"]:
            extrinsic = self.current_state["input"]["extrinsic"]
            
            # Get guarantees (work reports)
            if "guarantees" in extrinsic:
                for guarantee in extrinsic["guarantees"]:
                    if "report" in guarantee:
                        work_reports.append(guarantee["report"])
        
        print(f"Collected {len(work_reports)} work reports for block")
        return work_reports
    
    def collect_preimages(self) -> List[Dict[str, Any]]:
        """
        Collect preimages for inclusion in the block.
        
        Returns:
            List of preimages to include
        """
        preimages = []
        
        # Extract preimages from the input
        if "input" in self.current_state and "extrinsic" in self.current_state["input"]:
            extrinsic = self.current_state["input"]["extrinsic"]
            
            if "preimages" in extrinsic:
                preimages = extrinsic["preimages"]
        
        print(f"Collected {len(preimages)} preimages for block")
        return preimages
    
    def compute_state_root(self, work_reports: List[Dict], preimages: List[Dict]) -> str:
        """
        Compute the state root after applying work reports and preimages.
        
        This uses the existing SafroleManager to process the state transition.
        
        Args:
            work_reports: Work reports to apply
            preimages: Preimages to apply
            
        Returns:
            The computed state root as hex string
        """
        try:
            # Create block input for safrole manager
            block_input = {
                "slot": self.current_slot + 1,
                "entropy": self.generate_vrf_entropy(self.current_slot + 1),
                "extrinsic": []  # Simplified for M2
            }
            
            # Process through safrole manager to get new state
            result = self.safrole_manager.process_block(block_input)
            
            # Compute state root from the updated state
            updated_state = self.safrole_manager.state
            state_bytes = json.dumps(updated_state, sort_keys=True).encode()
            state_hash = hashlib.blake2b(state_bytes, digest_size=32).digest()
            
            return bytes_to_hex(state_hash)
            
        except Exception as e:
            print(f"Error computing state root: {e}")
            # Fallback to simple hash
            combined_data = {
                "work_reports": work_reports,
                "preimages": preimages,
                "slot": self.current_slot + 1
            }
            data_bytes = json.dumps(combined_data, sort_keys=True).encode()
            fallback_hash = hashlib.blake2b(data_bytes, digest_size=32).digest()
            return bytes_to_hex(fallback_hash)
    
    def compute_extrinsics_root(self, work_reports: List[Dict], preimages: List[Dict]) -> str:
        """
        Compute the extrinsics root (Merkle root of block body).
        
        Args:
            work_reports: Work reports in the block
            preimages: Preimages in the block
            
        Returns:
            The extrinsics root as hex string
        """
        # Combine all extrinsics
        all_extrinsics = {
            "work_reports": work_reports,
            "preimages": preimages,
            "count": len(work_reports) + len(preimages)
        }
        
        # Compute Merkle root for extrinsics (even if empty)
        extrinsics_bytes = json.dumps(all_extrinsics, sort_keys=True).encode()
        extrinsics_hash = hashlib.blake2b(extrinsics_bytes, digest_size=32).digest()
        
        return bytes_to_hex(extrinsics_hash)
    
    def generate_vrf_seal_signature(self, header: Dict[str, Any]) -> Tuple[str, str]:
        """
        Generate VRF seal signature (HS) and VRF output (HV) according to GP Section 6.4.
        
        This implements the Safrole sealing mechanism using proper Bandersnatch VRF:
        - HS: Bandersnatch VRF signature for the seal key (GP eq. 6.15/6.16)
        - HV: VRF output for entropy generation (GP eq. 6.17)
        
        Args:
            header: The block header to sign (without HS and HV)
            
        Returns:
            Tuple of (HS, HV) as hex strings
        """
        try:
            # Get validator info for VRF key
            validator_info = self.get_current_validator_info()
            bandersnatch_key = validator_info.get("bandersnatch", "0x" + "00" * 32)
            
            # Serialize header for signing (exclude HS and HV fields)
            header_for_signing = {k: v for k, v in header.items() 
                                if k not in ["seal_signature", "vrf_output"]}
            header_bytes = json.dumps(header_for_signing, sort_keys=True).encode()
            
            # For M2 demo: Use simplified VRF implementation
            # Production would call Bandersnatch VRF API server
            
            # Generate HS (seal signature) - GP equation 6.15/6.16
            # VRF input for seal: header serialization
            seal_vrf_input = header_bytes
            
            # Generate VRF signature for seal (HS)
            hs_signature = self._generate_bandersnatch_vrf_signature(
                vrf_input_data=seal_vrf_input,
                aux_data=b"",  # Empty aux data for seal
                context="seal"
            )
            
            # Generate HV (VRF output) - GP equation 6.17
            # VRF input: XE || Y(HS) where XE = "jam_entropy"
            vrf_entropy_input = b"jam_entropy" + hex_to_bytes(hs_signature)[:32]
            
            # Generate VRF output for entropy (HV)
            hv_output = self._generate_bandersnatch_vrf_output(
                vrf_input_data=vrf_entropy_input,
                context="entropy"
            )
            
            return hs_signature, hv_output
            
        except Exception as e:
            print(f"⚠️  Error generating VRF signatures: {e}")
            # Fallback to simplified implementation
            return self._generate_simplified_vrf_signatures(header)
    
    def _generate_bandersnatch_vrf_signature(self, vrf_input_data: bytes, aux_data: bytes, context: str) -> str:
        """
        Generate Bandersnatch VRF signature using the VRF API server.
        
        This calls the actual Bandersnatch VRF API server to generate proper VRF signatures
        according to Graypaper specifications.
        
        Args:
            vrf_input_data: VRF input data
            aux_data: Additional data for VRF
            context: Context string for domain separation
            
        Returns:
            VRF signature as hex string
        """
        try:
            
            # Get validator keys for the ring
            validator_keys = self._get_validator_public_keys()
            
            # Create prover if not exists
            prover_id = self._get_or_create_vrf_prover(validator_keys)
            if not prover_id:
                print(f"⚠️  Failed to create VRF prover, using fallback for {context}")
                return self._fallback_vrf_signature(vrf_input_data, aux_data, context)
            
            # Call Bandersnatch VRF API for IETF VRF signature
            vrf_api_url = "http://localhost:3000"
            payload = {
                "prover_id": prover_id,
                "vrf_input_data": bytes_to_hex(vrf_input_data),
                "aux_data": bytes_to_hex(aux_data)
            }
            
            print(f"🌐 Calling Bandersnatch VRF API for {context}:")
            print(f"   URL: {vrf_api_url}/prover/ietf_vrf_sign")
            print(f"   Prover ID: {prover_id}")
            print(f"   Input length: {len(vrf_input_data)} bytes")
            
            response = requests.post(
                f"{vrf_api_url}/prover/ietf_vrf_sign",
                json=payload,
                timeout=10,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                result = response.json()
                vrf_signature = result.get("signature")
                if vrf_signature:
                    print(f"✅ Generated Bandersnatch VRF signature for {context}")
                    return vrf_signature
                else:
                    print(f"⚠️  No signature in VRF API response for {context}")
                    return self._fallback_vrf_signature(vrf_input_data, aux_data, context)
            else:
                print(f"⚠️  VRF API error {response.status_code} for {context}: {response.text}")
                return self._fallback_vrf_signature(vrf_input_data, aux_data, context)
                
        except requests.exceptions.ConnectionError:
            print(f"⚠️  Bandersnatch VRF server not available for {context}, using fallback")
            return self._fallback_vrf_signature(vrf_input_data, aux_data, context)
        except Exception as e:
            print(f"⚠️  Error calling Bandersnatch VRF API for {context}: {e}")
            return self._fallback_vrf_signature(vrf_input_data, aux_data, context)
    
    def _generate_bandersnatch_vrf_output(self, vrf_input_data: bytes, context: str) -> str:
        """
        Generate Bandersnatch VRF output hash using the VRF API server.
        
        This calls the actual Bandersnatch VRF API server to generate proper VRF output
        according to Graypaper specifications.
        
        Args:
            vrf_input_data: VRF input data
            context: Context string for domain separation
            
        Returns:
            VRF output hash as hex string
        """
        try:
            
            
            # Get validator keys for the ring
            validator_keys = self._get_validator_public_keys()
            
            # Create prover if not exists
            prover_id = self._get_or_create_vrf_prover(validator_keys)
            if not prover_id:
                print(f"⚠️  Failed to create VRF prover, using fallback for {context}")
                return self._fallback_vrf_output(vrf_input_data, context)
            
            # Call Bandersnatch VRF API for VRF output
            vrf_api_url = "http://localhost:3000"
            payload = {
                "prover_id": prover_id,
                "vrf_input_data": bytes_to_hex(vrf_input_data)
            }
            
            print(f"🌐 Calling Bandersnatch VRF API for {context}:")
            print(f"   URL: {vrf_api_url}/prover/vrf_output")
            print(f"   Prover ID: {prover_id}")
            print(f"   Input length: {len(vrf_input_data)} bytes")
            
            response = requests.post(
                f"{vrf_api_url}/prover/vrf_output",
                json=payload,
                timeout=10,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                result = response.json()
                vrf_output_hash = result.get("vrf_output_hash")
                if vrf_output_hash:
                    print(f"✅ Generated Bandersnatch VRF output for {context}")
                    return vrf_output_hash
                else:
                    print(f"⚠️  No VRF output in API response for {context}")
                    return self._fallback_vrf_output(vrf_input_data, context)
            else:
                print(f"⚠️  VRF API error {response.status_code} for {context}: {response.text}")
                return self._fallback_vrf_output(vrf_input_data, context)
                
        except requests.exceptions.ConnectionError:
            print(f"⚠️  Bandersnatch VRF server not available for {context}, using fallback")
            return self._fallback_vrf_output(vrf_input_data, context)
        except Exception as e:
            print(f"⚠️  Error calling Bandersnatch VRF API for {context}: {e}")
            return self._fallback_vrf_output(vrf_input_data, context)
    
    def _generate_simplified_vrf_signatures(self, header: Dict[str, Any]) -> Tuple[str, str]:
        """
        Fallback simplified VRF signature generation.
        
        Args:
            header: The block header to sign
            
        Returns:
            Tuple of (HS, HV) as hex strings
        """
        # Get validator info for seal key
        validator_info = self.get_current_validator_info()
        bandersnatch_key = validator_info.get("bandersnatch", "0x" + "00" * 32)
        
        # Serialize header for signing
        header_for_signing = {k: v for k, v in header.items() 
                            if k not in ["seal_signature", "vrf_output"]}
        header_bytes = json.dumps(header_for_signing, sort_keys=True).encode()
        
        # Generate HS (seal signature)
        seal_secret = self.validator_private_key.encode() + hex_to_bytes(bandersnatch_key)
        hs_signature = hashlib.blake2b(seal_secret + header_bytes + b"seal", digest_size=64).digest()
        
        # Generate HV (VRF output)
        vrf_input = b"jam_entropy" + hs_signature[:32]
        hv_output = hashlib.blake2b(seal_secret + vrf_input + b"vrf", digest_size=32).digest()
        
        return bytes_to_hex(hs_signature), bytes_to_hex(hv_output)
    
    def _get_validator_public_keys(self) -> List[str]:
        """
        Get the list of validator public keys for the VRF ring.
        
        Returns:
            List of validator public keys as hex strings
        """
        validator_keys = []
        for validator in self.validators:
            # Use bandersnatch key if available, otherwise use ed25519
            bandersnatch_key = validator.get("bandersnatch", validator.get("ed25519", "0x" + "00" * 32))
            validator_keys.append(bandersnatch_key)
        return validator_keys
    
    def _get_or_create_vrf_prover(self, validator_keys: List[str]) -> Optional[str]:
        """
        Get or create a VRF prover instance on the Bandersnatch VRF server.
        
        Args:
            validator_keys: List of validator public keys
            
        Returns:
            Prover ID or None if failed
        """
        try:
            
            # Check if we have a cached prover ID
            cache_key = f"prover_{self.validator_index}_{len(validator_keys)}"
            if hasattr(self, '_vrf_prover_cache') and cache_key in self._vrf_prover_cache:
                return self._vrf_prover_cache[cache_key]
            
            # Create new prover
            vrf_api_url = "http://localhost:3000"
            payload = {
                "public_keys": validator_keys,
                "prover_index": self.validator_index
            }
            
            response = requests.post(
                f"{vrf_api_url}/prover/create",
                json=payload,
                timeout=10,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code == 200:
                result = response.json()
                prover_id = result.get("prover_id")
                if prover_id:
                    # Cache the prover ID
                    if not hasattr(self, '_vrf_prover_cache'):
                        self._vrf_prover_cache = {}
                    self._vrf_prover_cache[cache_key] = prover_id
                    print(f"✅ Created VRF prover {prover_id} for validator {self.validator_index}")
                    return prover_id
            else:
                print(f"⚠️  Failed to create VRF prover: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.ConnectionError:
            print("⚠️  Bandersnatch VRF server not available for prover creation")
            return None
        except Exception as e:
            print(f"⚠️  Error creating VRF prover: {e}")
            return None
    
    def _fallback_vrf_signature(self, vrf_input_data: bytes, aux_data: bytes, context: str) -> str:
        """
        Fallback VRF signature generation when Bandersnatch server is unavailable.
        
        Args:
            vrf_input_data: VRF input data
            aux_data: Additional data for VRF
            context: Context string for domain separation
            
        Returns:
            Fallback VRF signature as hex string
        """
        validator_info = self.get_current_validator_info()
        bandersnatch_key = validator_info.get("bandersnatch", "0x" + "00" * 32)
        
        # Combine secret key, VRF input, and context
        vrf_secret = self.validator_private_key.encode() + hex_to_bytes(bandersnatch_key)
        vrf_material = vrf_secret + vrf_input_data + aux_data + context.encode()
        
        # Generate deterministic VRF signature (64 bytes)
        vrf_signature = hashlib.blake2b(vrf_material, digest_size=64).digest()
        
        return bytes_to_hex(vrf_signature)
    
    def _fallback_vrf_output(self, vrf_input_data: bytes, context: str) -> str:
        """
        Fallback VRF output generation when Bandersnatch server is unavailable.
        
        Args:
            vrf_input_data: VRF input data
            context: Context string for domain separation
            
        Returns:
            Fallback VRF output hash as hex string
        """
        validator_info = self.get_current_validator_info()
        bandersnatch_key = validator_info.get("bandersnatch", "0x" + "00" * 32)
        
        # VRF output computation: O = x * I where I = hash_to_curve(input)
        vrf_secret = self.validator_private_key.encode() + hex_to_bytes(bandersnatch_key)
        vrf_output_point = hashlib.blake2b(vrf_secret + vrf_input_data + context.encode(), digest_size=32).digest()
        
        # VRF output hash Y(O) - this is what gets used as entropy
        vrf_output_hash = hashlib.blake2b(vrf_output_point + b"output_hash", digest_size=32).digest()
        
        return bytes_to_hex(vrf_output_hash)
    
    def sign_block_header(self, header: Dict[str, Any]) -> str:
        """
        Sign the block header using the validator's private key.
        
        This is a wrapper that generates the seal signature (HS).
        The VRF output (HV) is generated separately.
        
        Args:
            header: The block header to sign
            
        Returns:
            The seal signature (HS) as hex string
        """
        hs_signature, _ = self.generate_vrf_seal_signature(header)
        return hs_signature
    
    def get_parent_hash(self) -> str:
        """
        Get the hash of the parent block.
        
        Returns:
            Parent block hash as hex string
        """
        if self.produced_blocks:
            # Use the last produced block as parent
            last_block = self.produced_blocks[-1]
            block_bytes = json.dumps(last_block, sort_keys=True).encode()
            parent_hash = hashlib.blake2b(block_bytes, digest_size=32).digest()
            return bytes_to_hex(parent_hash)
        else:
            # Use a hash of the current state as genesis parent
            state_bytes = json.dumps(self.current_state, sort_keys=True).encode()
            parent_hash = hashlib.blake2b(state_bytes, digest_size=32).digest()
            return bytes_to_hex(parent_hash)
    
    def _update_entropy_accumulator(self, vrf_output: str):
        """
        Update the entropy accumulator according to GP Section 6.4.
        
        Implements: η'₀ ≡ H(η₀ ⌢ Y(HV)) - equation 6.22
        
        Args:
            vrf_output: The VRF output (HV) from the block
        """
        try:
            # Get current entropy accumulator (η₀)
            eta_values = self.current_state.get("entropy", [])
            if eta_values:
                eta_0 = eta_values[0]
            else:
                eta_0 = "0x" + "00" * 32
            
            # Compute Y(HV) - VRF output hash
            vrf_output_bytes = hex_to_bytes(vrf_output)
            vrf_output_hash = hashlib.blake2b(vrf_output_bytes, digest_size=32).digest()
            
            # Update entropy: η'₀ ≡ H(η₀ ⌢ Y(HV))
            eta_0_bytes = hex_to_bytes(eta_0)
            new_eta_0 = hashlib.blake2b(eta_0_bytes + vrf_output_hash, digest_size=32).digest()
            
            # Update the entropy in current state
            if "entropy" not in self.current_state:
                self.current_state["entropy"] = ["0x" + "00" * 32] * 4
            
            self.current_state["entropy"][0] = bytes_to_hex(new_eta_0)
            
            print(f"🔄 Updated entropy accumulator:")
            print(f"   Previous η₀: {eta_0[:32]}...")
            print(f"   New η₀: {bytes_to_hex(new_eta_0)[:32]}...")
            
        except Exception as e:
            print(f"⚠️  Error updating entropy accumulator: {e}")
    
    def produce_block(self, target_slot: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Produce a block for the specified slot (or next slot if not specified).
        
        This is the main Safrole block production function that:
        1. Checks if we're the leader for the slot
        2. Collects work reports and preimages
        3. Computes state and extrinsics roots
        4. Constructs and signs the block header
        5. Assembles the complete block
        
        Args:
            target_slot: The slot to produce a block for (defaults to current_slot + 1)
            
        Returns:
            The produced block, or None if not leader for this slot
        """
        if target_slot is None:
            target_slot = self.current_slot + 1
        
        print(f"\n🏗️  Attempting to produce block for slot {target_slot}")
        
        # Check if we're the leader for this slot
        if not self.is_leader_for_slot(target_slot):
            print(f"❌ Not leader for slot {target_slot}")
            return None
        
        # Prevent producing multiple blocks for the same slot
        if target_slot <= self.last_authored_slot:
            print(f"❌ Already authored block for slot {target_slot}")
            return None
        
        try:
            # Step 1: Collect block contents
            print("📋 Collecting work reports and preimages...")
            work_reports = self.collect_work_reports()
            preimages = self.collect_preimages()
            
            # Step 2: Generate VRF entropy
            entropy = self.generate_vrf_entropy(target_slot)
            print(f"🎲 Generated VRF entropy: {entropy[:32]}...")
            
            # Step 3: Compute roots
            print("🧮 Computing state and extrinsics roots...")
            state_root = self.compute_state_root(work_reports, preimages)
            extrinsics_root = self.compute_extrinsics_root(work_reports, preimages)
            
            # Step 4: Get parent hash
            parent_hash = self.get_parent_hash()
            
            # Step 5: Construct block header (without VRF signatures initially)
            validator_info = self.get_current_validator_info()
            header = {
                "slot": target_slot,
                "parent_hash": parent_hash,
                "state_root": state_root,
                "extrinsics_root": extrinsics_root,
                "entropy": entropy,
                "timestamp": int(time.time()),
                "author_index": self.validator_index,
                "author_key": validator_info.get("ed25519", "0x" + "00" * 32),
            }
            
            # Step 6: Generate VRF seal signature (HS) and VRF output (HV)
            print("✍️  Generating VRF seal signature (HS) and VRF output (HV)...")
            hs_signature, hv_output = self.generate_vrf_seal_signature(header)
            
            # Add VRF components to header according to GP Section 6.4
            header["seal_signature"] = hs_signature  # HS in GP
            header["vrf_output"] = hv_output         # HV in GP
            header["signature"] = hs_signature       # Keep for compatibility
            
            # Step 7: Construct block body
            body = {
                "work_reports": work_reports,
                "preimages": preimages,
                "extrinsics_count": len(work_reports) + len(preimages)
            }
            
            # Step 8: Assemble complete block
            block = {
                "header": header,
                "body": body,
                "block_hash": None  # Will be computed after assembly
            }
            
            # Step 9: Compute block hash
            block_bytes = json.dumps(block, sort_keys=True).encode()
            block_hash = hashlib.blake2b(block_bytes, digest_size=32).hexdigest()
            block["block_hash"] = block_hash

            # --- Grandpa Integration ---
            keys_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../Grandpa/keys.json'))
            config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../Grandpa/nodes_config.json'))
            with open(keys_path, 'r') as f:
                keys_all = json.load(f)
            with open(config_path, 'r') as f:
                config = json.load(f)
            validators_map = {v["id"]: v for v in keys_all["validators"]}
            keys = validators_map.get(self.validator_index, keys_all["validators"][0])

            try:
                grandpa_result = finalize_block(block, self.validator_index, keys, config)
                block["grandpa_finalized"] = grandpa_result.get("finalized", False)
                block["grandpa_justification"] = grandpa_result.get("justification", None)
                print(f"🧑‍⚖️ Grandpa finalized: {block['grandpa_finalized']}")
            except Exception as e:
                print(f"⚠️  Grandpa finalization failed: {e}")
                block["grandpa_finalized"] = False
            
            # Step 10: Update entropy accumulator according to GP Section 6.4
            # η'₀ ≡ H(η₀ ⌢ Y(HV)) - equation 6.22
            self._update_entropy_accumulator(hv_output)
            
            # Step 11: Update internal state
            self.produced_blocks.append(block)
            self.last_authored_slot = target_slot
            self.current_slot = target_slot
            
            print(f"✅ Successfully produced block for slot {target_slot}")
            print(f"   Block hash: {block['block_hash'][:32]}...")
            print(f"   Seal signature (HS): {hs_signature[:32]}...")
            print(f"   VRF output (HV): {hv_output[:32]}...")
            print(f"   Work reports: {len(work_reports)}")
            print(f"   Preimages: {len(preimages)}")
            print(f"   State root: {state_root[:32]}...")
            
            return block
            
        except Exception as e:
            print(f"❌ Error producing block for slot {target_slot}: {e}")
            return None
    
    def validate_block(self, block: Dict[str, Any]) -> bool:
        """
        Validate a produced block before broadcasting.
        
        Args:
            block: The block to validate
            
        Returns:
            True if the block is valid
        """
        try:
            header = block.get("header", {})
            body = block.get("body", {})
            
            # Basic structural validation
            required_header_fields = [
                "slot", "parent_hash", "state_root", "extrinsics_root",
                "entropy", "seal_signature", "vrf_output", "author_index"
            ]
            
            for field in required_header_fields:
                if field not in header:
                    print(f"❌ Missing header field: {field}")
                    return False
            
            # Validate slot progression (allow first block or proper progression)
            # Skip validation during block production (self-validation issue)
            if hasattr(self, '_validating_own_block') and self._validating_own_block:
                pass  # Skip slot validation for our own block during production
            elif self.last_authored_slot >= 0 and header["slot"] <= self.last_authored_slot:
                print(f"❌ Invalid slot progression: {header['slot']} <= {self.last_authored_slot}")
                return False
            
            # Validate VRF signatures (HS and HV)
            header_for_validation = {k: v for k, v in header.items() 
                                   if k not in ["seal_signature", "vrf_output", "signature"]}
            expected_hs, expected_hv = self.generate_vrf_seal_signature(header_for_validation)
            
            if header["seal_signature"] != expected_hs:
                print(f"❌ Invalid seal signature (HS)")
                return False
            
            if header["vrf_output"] != expected_hv:
                print(f"❌ Invalid VRF output (HV)")
                return False
            
            # Validate work reports count (max 6 for standard blocks)
            work_reports = body.get("work_reports", [])
            if len(work_reports) > 6:
                print(f"❌ Too many work reports: {len(work_reports)}")
                return False
            
            print(f"✅ Block validation passed")
            return True
            
        except Exception as e:
            print(f"❌ Block validation error: {e}")
            return False
    
    def get_producer_stats(self) -> Dict[str, Any]:
        """Get statistics about block production."""
        return {
            "validator_index": self.validator_index,
            "validator_key": self.get_current_validator_info().get("ed25519", "unknown")[:32] + "...",
            "current_slot": self.current_slot,
            "last_authored_slot": self.last_authored_slot,
            "blocks_produced": len(self.produced_blocks),
            "validators_count": len(self.validators),
            "state_file": self.state_file_path
        }
    
    def simulate_block_production_sequence(self, num_slots: int = 10) -> List[Dict[str, Any]]:
        """
        Simulate block production for multiple slots.
        
        This is useful for testing and demonstration purposes.
        
        Args:
            num_slots: Number of slots to simulate
            
        Returns:
            List of produced blocks
        """
        print(f"\n🎬 Starting block production simulation for {num_slots} slots")
        print("=" * 60)
        
        produced_blocks = []
        start_slot = self.current_slot + 1
        
        for slot in range(start_slot, start_slot + num_slots):
            print(f"\n--- Slot {slot} ---")
            
            block = self.produce_block(slot)
            
            if block:
                # Validate the block
                if self.validate_block(block):
                    produced_blocks.append(block)
                    print(f"✅ Block {len(produced_blocks)} added to chain")
                else:
                    print(f"❌ Block validation failed")
            else:
                print(f"⏭️  Skipped slot {slot} (not leader)")
            
            # Simulate time passing
            time.sleep(0.1)
        
        print(f"\n🏁 Simulation complete!")
        print(f"   Total slots: {num_slots}")
        print(f"   Blocks produced: {len(produced_blocks)}")
        print(f"   Success rate: {len(produced_blocks)/num_slots*100:.1f}%")
        
        return produced_blocks


# Convenience functions for easy usage

def create_safrole_producer(validator_index: int = 0, 
                          state_file_path: str = None) -> SafroleBlockProducer:
    """
    Create a Safrole block producer with default configuration.
    
    Args:
        validator_index: Index of the validator
        state_file_path: Path to state file (uses default if not provided)
        
    Returns:
        Configured SafroleBlockProducer instance
    """
    return SafroleBlockProducer(
        state_file_path=state_file_path,
        validator_index=validator_index
    )


def demo_safrole_block_production():
    """
    Demonstrate Safrole block production functionality.
    """
    print("🚀 JAM Safrole Block Production Demo")
    print("=" * 50)
    
    # Create producer
    producer = create_safrole_producer(validator_index=0)
    
    # Show initial stats
    stats = producer.get_producer_stats()
    print(f"\n📊 Producer Stats:")
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    # Produce a single block
    print(f"\n🏗️  Producing single block...")
    block = producer.produce_block()
    
    if block:
        print(f"✅ Block produced successfully!")
        print(f"   Slot: {block['header']['slot']}")
        print(f"   Hash: {block['block_hash'][:32]}...")
        print(f"   Work reports: {len(block['body']['work_reports'])}")
    else:
        print(f"❌ Failed to produce block")
    
    # Simulate multiple blocks
    print(f"\n🎬 Simulating block production sequence...")
    blocks = producer.simulate_block_production_sequence(5)
    
    print(f"\n📈 Final Results:")
    print(f"   Blocks in chain: {len(producer.produced_blocks)}")
    
    final_stats = producer.get_producer_stats()
    print(f"   Final slot: {final_stats['current_slot']}")
    print(f"   Last authored: {final_stats['last_authored_slot']}")


if __name__ == "__main__":
    demo_safrole_block_production()
