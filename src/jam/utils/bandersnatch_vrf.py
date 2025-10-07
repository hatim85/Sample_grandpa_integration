"""
Bandersnatch VRF Integration

This module provides integration with the Bandersnatch VRF API server
for proper VRF signature generation according to Graypaper specifications.
"""

import requests
import json
import hashlib
from typing import Dict, List, Any, Optional, Tuple
from .helpers import bytes_to_hex, hex_to_bytes


class BandersnatchVRFClient:
    """
    Client for interacting with the Bandersnatch VRF API server.
    
    This client provides methods to:
    - Create provers and verifiers
    - Generate VRF signatures and outputs
    - Verify VRF signatures
    """
    
    def __init__(self, api_url: str = "http://localhost:3000"):
        """
        Initialize the VRF client.
        
        Args:
            api_url: URL of the Bandersnatch VRF API server
        """
        self.api_url = api_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
    
    def is_server_available(self) -> bool:
        """
        Check if the Bandersnatch VRF API server is available.
        
        Returns:
            True if server is available, False otherwise
        """
        try:
            response = self.session.get(f"{self.api_url}/", timeout=2)
            return response.status_code == 200
        except Exception:
            return False
    
    def create_prover(self, public_keys: List[str], prover_index: int) -> Optional[Dict[str, Any]]:
        """
        Create a prover instance on the VRF server.
        
        Args:
            public_keys: List of public keys in the ring
            prover_index: Index of the prover in the ring
            
        Returns:
            Prover creation response or None if failed
        """
        try:
            payload = {
                "public_keys": public_keys,
                "prover_index": prover_index
            }
            
            response = self.session.post(
                f"{self.api_url}/prover/create",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Failed to create prover: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"Error creating prover: {e}")
            return None
    
    def generate_vrf_output(self, prover_id: str, vrf_input_data: str) -> Optional[str]:
        """
        Generate VRF output hash using the VRF server.
        
        Args:
            prover_id: ID of the prover instance
            vrf_input_data: VRF input data as hex string
            
        Returns:
            VRF output hash as hex string or None if failed
        """
        try:
            payload = {
                "prover_id": prover_id,
                "vrf_input_data": vrf_input_data
            }
            
            response = self.session.post(
                f"{self.api_url}/prover/vrf_output",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("vrf_output_hash")
            else:
                print(f"Failed to generate VRF output: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"Error generating VRF output: {e}")
            return None
    
    def generate_ietf_vrf_signature(self, prover_id: str, vrf_input_data: str, aux_data: str = "") -> Optional[str]:
        """
        Generate IETF VRF signature using the VRF server.
        
        Args:
            prover_id: ID of the prover instance
            vrf_input_data: VRF input data as hex string
            aux_data: Additional data as hex string
            
        Returns:
            VRF signature as hex string or None if failed
        """
        try:
            payload = {
                "prover_id": prover_id,
                "vrf_input_data": vrf_input_data,
                "aux_data": aux_data
            }
            
            response = self.session.post(
                f"{self.api_url}/prover/ietf_vrf_sign",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("signature")
            else:
                print(f"Failed to generate IETF VRF signature: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"Error generating IETF VRF signature: {e}")
            return None
    
    def generate_ring_vrf_signature(self, prover_id: str, vrf_input_data: str, aux_data: str = "") -> Optional[str]:
        """
        Generate Ring VRF signature using the VRF server.
        
        Args:
            prover_id: ID of the prover instance
            vrf_input_data: VRF input data as hex string
            aux_data: Additional data as hex string
            
        Returns:
            Ring VRF signature as hex string or None if failed
        """
        try:
            payload = {
                "prover_id": prover_id,
                "vrf_input_data": vrf_input_data,
                "aux_data": aux_data
            }
            
            response = self.session.post(
                f"{self.api_url}/prover/ring_vrf_sign",
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("signature")
            else:
                print(f"Failed to generate Ring VRF signature: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"Error generating Ring VRF signature: {e}")
            return None


class SafroleVRFHelper:
    """
    Helper class for Safrole VRF operations according to Graypaper specifications.
    
    This class provides high-level methods for:
    - Generating HS (seal signatures) 
    - Generating HV (VRF outputs)
    - Integrating with the Bandersnatch VRF server
    """
    
    def __init__(self, vrf_client: Optional[BandersnatchVRFClient] = None):
        """
        Initialize the Safrole VRF helper.
        
        Args:
            vrf_client: Bandersnatch VRF client (optional)
        """
        self.vrf_client = vrf_client or BandersnatchVRFClient()
        self.prover_cache: Dict[str, str] = {}  # Cache prover IDs
    
    def generate_safrole_vrf_signatures(
        self, 
        validator_keys: List[str],
        validator_index: int,
        header_data: bytes,
        entropy_context: bytes
    ) -> Tuple[str, str]:
        """
        Generate Safrole VRF signatures (HS and HV) according to Graypaper.
        
        Args:
            validator_keys: List of validator public keys
            validator_index: Index of the current validator
            header_data: Serialized header data for signing
            entropy_context: Context data for entropy generation
            
        Returns:
            Tuple of (HS, HV) as hex strings
        """
        try:
            # Check if VRF server is available
            if not self.vrf_client.is_server_available():
                print("⚠️  Bandersnatch VRF server not available, using simplified VRF")
                return self._generate_simplified_vrf(header_data, entropy_context)
            
            # Create or get cached prover
            prover_id = self._get_or_create_prover(validator_keys, validator_index)
            if not prover_id:
                print("⚠️  Failed to create VRF prover, using simplified VRF")
                return self._generate_simplified_vrf(header_data, entropy_context)
            
            # Generate HS (seal signature) - GP equation 6.15/6.16
            header_hex = bytes_to_hex(header_data)
            hs_signature = self.vrf_client.generate_ietf_vrf_signature(
                prover_id=prover_id,
                vrf_input_data=header_hex,
                aux_data=""  # Empty aux data for seal
            )
            
            if not hs_signature:
                print("⚠️  Failed to generate HS signature, using simplified VRF")
                return self._generate_simplified_vrf(header_data, entropy_context)
            
            # Generate HV (VRF output) - GP equation 6.17
            # VRF input: XE || Y(HS) where XE = "jam_entropy"
            vrf_entropy_input = b"jam_entropy" + hex_to_bytes(hs_signature)[:32]
            entropy_hex = bytes_to_hex(vrf_entropy_input)
            
            hv_output = self.vrf_client.generate_vrf_output(
                prover_id=prover_id,
                vrf_input_data=entropy_hex
            )
            
            if not hv_output:
                print("⚠️  Failed to generate HV output, using simplified VRF")
                return self._generate_simplified_vrf(header_data, entropy_context)
            
            print("✅ Generated VRF signatures using Bandersnatch VRF server")
            return hs_signature, hv_output
            
        except Exception as e:
            print(f"⚠️  Error in Safrole VRF generation: {e}")
            return self._generate_simplified_vrf(header_data, entropy_context)
    
    def _get_or_create_prover(self, validator_keys: List[str], validator_index: int) -> Optional[str]:
        """
        Get or create a prover instance.
        
        Args:
            validator_keys: List of validator public keys
            validator_index: Index of the current validator
            
        Returns:
            Prover ID or None if failed
        """
        # Create cache key
        cache_key = f"{len(validator_keys)}_{validator_index}_{hash(tuple(validator_keys))}"
        
        # Check cache
        if cache_key in self.prover_cache:
            return self.prover_cache[cache_key]
        
        # Create new prover
        result = self.vrf_client.create_prover(validator_keys, validator_index)
        if result and result.get("prover_id"):
            prover_id = result["prover_id"]
            self.prover_cache[cache_key] = prover_id
            return prover_id
        
        return None
    
    def _generate_simplified_vrf(self, header_data: bytes, entropy_context: bytes) -> Tuple[str, str]:
        """
        Generate simplified VRF signatures as fallback.
        
        Args:
            header_data: Serialized header data
            entropy_context: Context data for entropy
            
        Returns:
            Tuple of (HS, HV) as hex strings
        """
        # Generate deterministic HS (seal signature)
        hs_signature = hashlib.blake2b(header_data + b"seal", digest_size=64).digest()
        
        # Generate deterministic HV (VRF output)
        vrf_input = b"jam_entropy" + hs_signature[:32] + entropy_context
        hv_output = hashlib.blake2b(vrf_input + b"vrf_output", digest_size=32).digest()
        
        return bytes_to_hex(hs_signature), bytes_to_hex(hv_output)


# Global VRF helper instance
_vrf_helper: Optional[SafroleVRFHelper] = None


def get_safrole_vrf_helper() -> SafroleVRFHelper:
    """
    Get the global Safrole VRF helper instance.
    
    Returns:
        SafroleVRFHelper instance
    """
    global _vrf_helper
    if _vrf_helper is None:
        _vrf_helper = SafroleVRFHelper()
    return _vrf_helper


def generate_safrole_vrf_signatures(
    validator_keys: List[str],
    validator_index: int,
    header_data: bytes,
    entropy_context: bytes = b""
) -> Tuple[str, str]:
    """
    Convenience function to generate Safrole VRF signatures.
    
    Args:
        validator_keys: List of validator public keys
        validator_index: Index of the current validator
        header_data: Serialized header data for signing
        entropy_context: Context data for entropy generation
        
    Returns:
        Tuple of (HS, HV) as hex strings
    """
    helper = get_safrole_vrf_helper()
    return helper.generate_safrole_vrf_signatures(
        validator_keys, validator_index, header_data, entropy_context
    )
