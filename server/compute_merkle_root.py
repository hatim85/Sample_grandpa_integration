#!/usr/bin/env python3
"""
compute_merkle_root.py

This script computes the Merkle root of a JAM state from a JSON file.
Usage: python compute_merkle_root.py <path_to_state.json>
"""

import hashlib
import json
import sys
from typing import Dict, List, Tuple, Optional

# ==============================================================================
# MERKLE TRIE COMPONENT
# ==============================================================================

def hash_func(data: bytes) -> bytes:
    """Computes the Blake2b-256 hash of the data."""
    return hashlib.blake2b(data, digest_size=32).digest()

def leaf_node(k: bytes, v: bytes) -> bytes:
    """Creates a 64-byte leaf node according to Appendix D.2."""
    if len(v) <= 32:
        # Embedded-value leaf
        head = 0b01000000 | (len(v) << 2)
        padded_value = v.ljust(32, b'\0')
        return bytes([head]) + k + padded_value
    else:
        # Regular leaf with a hashed value
        head = 0b11000000
        return bytes([head]) + k + hash_func(v)

def branch_node(l_hash: bytes, r_hash: bytes) -> bytes:
    """Creates a 64-byte branch node according to Appendix D.2."""
    head = l_hash[0] & 0b01111111  # First bit must be 0
    return bytes([head]) + l_hash[1:] + r_hash

def get_bit(key_bytes: bytes, index: int) -> bool:
    """Gets the bit at a specific index of a byte string."""
    byte_index = index // 8
    bit_index = index % 8
    if byte_index >= len(key_bytes):
        return False
    return (key_bytes[byte_index] >> bit_index) & 1

def merkle(kvs: List[Tuple[bytes, bytes]], i: int = 0) -> bytes:
    """Recursively computes the Merkle root for a list of key-value pairs."""
    if not kvs:
        return b'\0' * 32
    
    if len(kvs) == 1:
        k, v = kvs[0]
        encoded_leaf = leaf_node(k, v)
        return hash_func(encoded_leaf)
    
    left, right = [], []
    for k, v in kvs:
        if get_bit(k, i):
            right.append((k, v))
        else:
            left.append((k, v))
    
    # Sort the keys to ensure consistent ordering
    left_sorted = sorted(left, key=lambda x: x[0])
    right_sorted = sorted(right, key=lambda x: x[0])
    
    encoded_branch = branch_node(
        merkle(left_sorted, i + 1),
        merkle(right_sorted, i + 1)
    )
    return hash_func(encoded_branch)

# ==============================================================================
# STATE SERIALIZATION COMPONENT
# ==============================================================================

def state_key_constructor(chapter_index: int, service_index: int = None, storage_key: bytes = None) -> bytes:
    """
    Implements the state-key constructor 'C' from Appendix D.1.
    Returns a 31-byte key.
    """
    key = bytearray(31)
    if service_index is None and storage_key is None:
        # C(chapter_index) for top-level state
        key[0] = chapter_index
    elif service_index is not None and storage_key is None:
        # C(255, service_index) for service account data
        key[0] = 255  # Chapter 255 for service accounts
        key[1:5] = service_index.to_bytes(4, 'little')
    elif service_index is not None and storage_key is not None:
        # C(service_index, storage_key) for items in a service's storage
        service_bytes = service_index.to_bytes(4, 'little')
        hashed_key = hash_func(storage_key)
        combined = service_bytes + hashed_key
        key[:len(combined)] = combined
    else:
        raise ValueError("Invalid arguments for state key constructor")
    return bytes(key)

def safe_hex_to_bytes(hex_str):
    """Safely convert a hex string to bytes, handling various formats."""
    if not hex_str:
        return b''
    # Remove '0x' prefix if present
    if hex_str.startswith('0x'):
        hex_str = hex_str[2:]
    # Ensure the hex string has even length
    if len(hex_str) % 2 != 0:
        hex_str = '0' + hex_str
    try:
        return bytes.fromhex(hex_str)
    except ValueError as e:
        print(f"Warning: Invalid hex string '{hex_str}': {e}")
        return b''

def serialize_state(state_data: dict) -> Dict[bytes, bytes]:
    """
    Takes a JSON state object and serializes it into a key-value dictionary
    ready for the merkle() function, based on the actual state structure.
    """
    serialized_map = {}
    
    def process_validator(validator_data):
        """Helper to process a single validator's data."""
        value = b''
        value += safe_hex_to_bytes(validator_data.get('bandersnatch', ''))
        value += safe_hex_to_bytes(validator_data.get('ed25519', ''))
        value += safe_hex_to_bytes(validator_data.get('bls', ''))
        value += safe_hex_to_bytes(validator_data.get('metadata', ''))
        return value
    
    # --- Serialize Gamma K (Chapter 100) ---
    if state_data.get('gamma_k'):
        key = state_key_constructor(100)
        value = b''.join(process_validator(v) for v in state_data['gamma_k'])
        if value:
            serialized_map[key] = value
            print(f"Added gamma_k with key: 0x{key.hex()}, value length: {len(value)} bytes")
    
    # --- Serialize Kappa (Chapter 101) ---
    if state_data.get('kappa'):
        key = state_key_constructor(101)
        value = b''.join(process_validator(v) for v in state_data['kappa'])
        if value:
            serialized_map[key] = value
            print(f"Added kappa with key: 0x{key.hex()}, value length: {len(value)} bytes")
    
    # --- Serialize Lambda (Chapter 102) ---
    if state_data.get('lambda_'):
        key = state_key_constructor(102)
        value = b''.join(process_validator(v) for v in state_data['lambda_'])
        if value:
            serialized_map[key] = value
            print(f"Added lambda_ with key: 0x{key.hex()}, value length: {len(value)} bytes")
    
    # --- Serialize Gamma Z (Chapter 103) ---
    if state_data.get('gamma_z'):
        key = state_key_constructor(103)
        value = safe_hex_to_bytes(state_data['gamma_z'])
        if value:
            serialized_map[key] = value
            print(f"Added gamma_z with key: 0x{key.hex()}, value length: {len(value)} bytes")
    
    # --- Serialize Beta (Chapter 104) ---
    if state_data.get('beta'):
        key = state_key_constructor(104)
        value = b''
        for item in state_data['beta']:
            value += safe_hex_to_bytes(item.get('header_hash', ''))
            value += item.get('mmr', {}).get('count', 0).to_bytes(8, 'little')
            for peak in item.get('mmr', {}).get('peaks', []):
                value += safe_hex_to_bytes(peak)
            for report in item.get('reported', []):
                value += safe_hex_to_bytes(report.get('exports_root', ''))
                value += safe_hex_to_bytes(report.get('hash', ''))
            value += safe_hex_to_bytes(item.get('state_root', ''))
        if value:
            serialized_map[key] = value
            print(f"Added beta with key: 0x{key.hex()}, value length: {len(value)} bytes")
    
    # --- Serialize Global State (Chapter 105) ---
    if state_data.get('globalState', {}).get('serviceRegistry'):
        key = state_key_constructor(105)
        value = b''
        for path, data in state_data['globalState']['serviceRegistry'].items():
            value += path.encode('utf-8')
            value += safe_hex_to_bytes(data.get('codeHash', ''))
        if value:
            serialized_map[key] = value
            print(f"Added globalState with key: 0x{key.hex()}, value length: {len(value)} bytes")
    
    # --- Serialize Psi (Chapter 106) ---
    if state_data.get('psi'):
        key = state_key_constructor(106)
        value = b''
        for list_name in ['bad', 'good', 'offenders', 'wonky']:
            for item in state_data['psi'].get(list_name, []):
                value += safe_hex_to_bytes(item)
        if value:
            serialized_map[key] = value
            print(f"Added psi with key: 0x{key.hex()}, value length: {len(value)} bytes")
    
    # --- Serialize Eta (Chapter 107) ---
    if state_data.get('eta'):
        key = state_key_constructor(107)
        value = b''
        for item in state_data['eta']:
            if isinstance(item, str):
                value += safe_hex_to_bytes(item)
            elif isinstance(item, dict):
                # Handle dictionary items if needed
                pass
        if value:
            serialized_map[key] = value
            print(f"Added eta with key: 0x{key.hex()}, value length: {len(value)} bytes")
    
    return serialized_map

def debug_print_state_structure(state_data, indent=0):
    """Recursively print the structure of the state data for debugging."""
    if isinstance(state_data, dict):
        for key, value in state_data.items():
            print('  ' * indent + f"{key}:", end=' ')
            if isinstance(value, (dict, list)) and value:
                print()
                debug_print_state_structure(value, indent + 1)
            else:
                if isinstance(value, str) and len(value) > 50:
                    print(f"string[{len(value)}]")
                elif isinstance(value, (list, dict)) and not value:
                    print("[]" if isinstance(value, list) else "{}")
                else:
                    print(repr(value)[:100] + ('...' if len(repr(value)) > 100 else ''))
    elif isinstance(state_data, list):
        if state_data and len(state_data) > 0:
            print('  ' * indent + f"list[{len(state_data)} items]")
            if len(state_data) > 0:
                debug_print_state_structure(state_data[0], indent + 1)
                if len(state_data) > 1:
                    print('  ' * (indent + 1) + "...")
        else:
            print('  ' * indent + "[]")


    """
    Compute merkle root from state data dictionary.
    
    Args:
        state_data: The state data dictionary
        
    Returns:
        The computed merkle root as hex string
    """
    try:
        # Serialize the state into key-value pairs
        serialized_map = serialize_state(state_data)
        
        if not serialized_map:
            # Return a default hash for empty state
            return "0x" + ("00" * 32)
        
        # Convert to sorted list of tuples for consistent ordering
        kvs = sorted(serialized_map.items(), key=lambda x: x[0])
        
        # Compute merkle root
        root_hash = merkle(kvs)
        
        # Return as hex string
        return "0x" + root_hash.hex()
        
    except Exception as e:
        print(f"Error computing merkle root: {e}")
        # Return a default hash on error
        return "0x" + ("00" * 32)


def compute_merkle_root_from_data(state_data: dict) -> str:
    """
    Computes the Merkle root from state data dictionary.
    
    Args:
        state_data: Dictionary containing the state data
        
    Returns:
        Hex string representation of the Merkle root
    """
    try:
        # Serialize the state into key-value pairs
        serialized_map = serialize_state(state_data)
        
        if not serialized_map:
            # Return default hash if no data
            return "0x" + ("00" * 32)
        
        # Convert dictionary to sorted list of tuples for consistent ordering
        kvs = sorted(serialized_map.items(), key=lambda x: x[0])
        
        # Compute Merkle root
        state_root = merkle(kvs)
        
        return "0x" + state_root.hex()
        
    except Exception as e:
        print(f"Error computing merkle root from data: {e}")
        # Return a default hash on error
        return "0x" + ("00" * 32)


def main():
    """Main function to compute Merkle root from a state JSON file."""
    if len(sys.argv) != 2:
        print("Usage: python compute_merkle_root.py <path_to_state.json>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    print(f"Loading state from '{filepath}'...")
    
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
            pre_state = data.get('pre_state', {})
            
        if not pre_state:
            print("Error: 'pre_state' not found in the JSON file")
            print("Available top-level keys:", list(data.keys()))
            sys.exit(1)
            
        # Debug: Print the structure of the pre_state
        print("\nState structure:")
        print("-" * 80)
        debug_print_state_structure(pre_state)
        print("-" * 80)
            
        print("\nStep 1: Serializing the state into a key-value map...")
        serialized_map = serialize_state(pre_state)
        
        print(f"\nSerialized state into {len(serialized_map)} key-value pairs:")
        print("-" * 80)
        
        if not serialized_map:
            print("Warning: No key-value pairs were generated from the state.")
            print("This might indicate that the state structure doesn't match the expected format.")
            print("Available keys in pre_state:", list(pre_state.keys()))
        
        # Convert dictionary to sorted list of tuples for consistent ordering
        kvs = sorted(serialized_map.items(), key=lambda x: x[0])
        
        # Display each key-value pair
        for i, (key, value) in enumerate(kvs, 1):
            print(f"\nPair {i}:")
            print(f"  Key (hex):   0x{key.hex()}")
            print(f"  Key (int):   {int.from_bytes(key, 'big')}")
            print(f"  Value (hex): 0x{value.hex()}")
            print(f"  Value (len): {len(value)} bytes")
            if len(value) <= 64:  # Only show full value if it's not too long
                try:
                    print(f"  Value (str): {value.decode('utf-8', errors='replace')}")
                except:
                    pass
        
        if not kvs:
            print("\nNo key-value pairs to process. Cannot compute Merkle root.")
            sys.exit(1)
            
        print("\n" + "-" * 80)
        print("Step 2: Computing Merkle root...")
        state_root = merkle(kvs)
        
        print("\n" + "="*70)
        print(f"✅ Final State Root: 0x{state_root.hex()}")
        print("="*70)
        
    except FileNotFoundError:
        print(f"Error: File '{filepath}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Could not parse '{filepath}' as JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
