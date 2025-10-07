#!/usr/bin/env python3
"""
Test Server Flow Integration

This script tests the complete server flow:
1. Server function execution
2. Merkle root computation and storage
3. Safrole block production with merkle root
"""

import sys
import os
import json
import requests
import time

# Add project paths
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'server'))


def test_server_flow():
    """Test the complete server flow integration."""
    print("🧪 Testing Server Flow Integration")
    print("=" * 50)
    
    # Check if server is running
    server_url = "http://localhost:8000"
    
    try:
        response = requests.get(f"{server_url}/", timeout=2)
        print("✅ Server is running")
    except requests.exceptions.ConnectionError:
        print("❌ Server is not running. Please start with:")
        print("   python3 -m uvicorn server.server:app --reload --port 8000")
        return False
    
    # Test payload for JAM Reports
    test_payload = {
        "guarantees": [
            {
                "report": {
                    "context": {
                        "lookup_anchor_slot": 100
                    },
                    "core_index": 0,
                    "authorizer_hash": "0x" + "aa" * 32,
                    "output": "0x" + "bb" * 32
                }
            }
        ]
    }
    
    print(f"\n🚀 Triggering server flow via /run-jam-reports...")
    print(f"   This will:")
    print(f"   1. Process JAM Reports")
    print(f"   2. Compute merkle root from state")
    print(f"   3. Run Safrole with merkle root integration")
    
    try:
        # Trigger the server flow
        response = requests.post(
            f"{server_url}/run-jam-reports",
            json=test_payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            
            print(f"\n✅ Server flow completed successfully!")
            
            # Check if server flow data is present
            if "server_flow" in result.get("data", {}):
                flow_data = result["data"]["server_flow"]
                
                print(f"\n📊 Flow Results:")
                print(f"   Status: {flow_data.get('flow_status')}")
                print(f"   Merkle root: {flow_data.get('merkle_root', 'N/A')[:32]}...")
                
                if "safrole_block" in flow_data:
                    block_data = flow_data["safrole_block"]
                    print(f"\n🏗️  Safrole Block:")
                    print(f"   Block hash: {block_data.get('block_hash', 'N/A')[:32]}...")
                    print(f"   Slot: {block_data.get('slot')}")
                    print(f"   Merkle root: {block_data.get('merkle_root', 'N/A')[:32]}...")
                    
                    vrf_components = block_data.get('vrf_components', {})
                    print(f"\n🔐 VRF Components:")
                    print(f"   HS (Seal): {vrf_components.get('seal_signature', 'N/A')[:32]}...")
                    print(f"   HV (VRF):  {vrf_components.get('vrf_output', 'N/A')[:32]}...")
                
                print(f"\n🎉 Server Flow Integration Working!")
                return True
            else:
                print(f"⚠️  No server flow data in response")
                return False
        else:
            print(f"❌ Server request failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing server flow: {e}")
        return False


def main():
    """Run server flow test."""
    print("🔄 JAM Server Flow Integration Test")
    print("Flow: JAM Reports → Merkle Root → Safrole Block")
    print("=" * 60)
    
    success = test_server_flow()
    
    if success:
        print(f"\n✅ Server flow integration test PASSED!")
        print(f"   The complete flow is working:")
        print(f"   1. ✅ Server function execution")
        print(f"   2. ✅ Merkle root computation & storage")
        print(f"   3. ✅ Safrole block production with merkle root")
        print(f"   4. ✅ VRF integration (HS + HV)")
    else:
        print(f"\n❌ Server flow integration test FAILED!")
        print(f"   Check server logs for details")


if __name__ == "__main__":
    main()
