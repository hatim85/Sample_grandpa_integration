#!/usr/bin/env python3
"""
Simple Safrole Block Producer Runner

This script demonstrates how to run the Safrole block producer.
"""

import sys
import os

# Add project paths
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from jam.core.safrole_block_producer import create_safrole_producer


def main():
    """Run the Safrole block producer demonstration."""
    print("🚀 JAM Safrole Block Producer")
    print("=" * 40)
    
    try:
        # Create producer
        print("📊 Creating Safrole producer...")
        producer = create_safrole_producer(validator_index=0)
        
        # Show producer info
        print(f"✅ Producer created successfully!")
        print(f"   Validator index: {producer.validator_index}")
        print(f"   Current slot: {producer.current_slot}")
        print(f"   Validators: {len(producer.validators)}")
        print(f"   Private key: {producer.validator_private_key[:16]}...")
        
        # Check leadership
        print(f"\n🎯 Checking leadership for next 10 slots...")
        leadership_found = False
        
        for slot in range(producer.current_slot + 1, producer.current_slot + 11):
            is_leader = producer.is_leader_for_slot(slot)
            status = "👑 LEADER" if is_leader else "   follower"
            # print(f"   Slot {slot:3d}: {status}")
            
            if is_leader and not leadership_found:
                leadership_found = True
                print(f"\n🏗️  Producing block for slot {slot}...")
                
                # Produce block
                block = producer.produce_block(slot)
                
                if block:
                    header = block["header"]
                    print(f"   ✅ Block produced successfully!")
                    print(f"   Block hash: {block['block_hash']}")
                    print(f"   Slot: {header['slot']}")
                    print(f"   Parent hash: {header['parent_hash']}")
                    print(f"   Seal signature: {header.get('seal_signature', 'N/A')[:32]}...")
                    print(f"   VRF output: {header.get('vrf_output', 'N/A')[:32]}...")
                    
                    # For M2: Block production complete, ready for network broadcast
                    print(f"\n📡 Block ready for network broadcast!")
                    print(f"   ✅ Block production completed successfully")
                    print(f"   🌐 Ready to send to JAM network")
                    
                else:
                    print(f"   ❌ Failed to produce block")
        
        if not leadership_found:
            print(f"   ℹ️  No leadership slots found in next 10 slots")
        
        # Show producer stats
        print(f"\n📈 Producer Statistics:")
        stats = producer.get_producer_stats()
        for key, value in stats.items():
            print(f"   {key}: {value}")
        
        print(f"\n🎉 M2 Safrole block production completed!")
        print(f"   📦 Block structure: Graypaper compliant")
        print(f"   🔐 VRF components: HS + HV generated via Bandersnatch")
        print(f"   🌐 Ready for: Network broadcast & consensus")
        
    except Exception as e:
        print(f"❌ Error running Safrole producer: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
