"""
Safrole Integration with Existing Server

This module shows how to integrate the focused Safrole block producer
with the existing FastAPI server infrastructure.
"""

import sys
import os
import json
from typing import Dict, Any, Optional

# Add project paths
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from jam.core.safrole_block_producer import SafroleBlockProducer, create_safrole_producer


class SafroleServerIntegration:
    """
    Integration layer between Safrole block producer and the existing server.
    
    This class provides methods to:
    - Initialize Safrole producer with server state
    - Produce blocks on demand
    - Integrate with existing server endpoints
    """
    
    def __init__(self, state_file_path: str = None):
        """
        Initialize the Safrole integration.
        
        Args:
            state_file_path: Path to updated_state.json (uses default if not provided)
        """
        self.state_file_path = state_file_path or self._get_default_state_path()
        self.producer: Optional[SafroleBlockProducer] = None
        self.is_initialized = False
    
    def _get_default_state_path(self) -> str:
        """Get default path to updated_state.json."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(current_dir, "updated_state.json")
    
    def initialize_producer(self, validator_index: int = 0) -> Dict[str, Any]:
        """
        Initialize the Safrole block producer.
        
        Args:
            validator_index: Index of the validator to use
            
        Returns:
            Initialization result with status and info
        """
        try:
            self.producer = create_safrole_producer(
                validator_index=validator_index,
                state_file_path=self.state_file_path
            )
            self.is_initialized = True
            
            stats = self.producer.get_producer_stats()
            
            return {
                "success": True,
                "message": "Safrole producer initialized successfully",
                "producer_stats": stats
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to initialize Safrole producer: {str(e)}",
                "error": str(e)
            }
    
    def check_leadership(self, slot: int = None) -> Dict[str, Any]:
        """
        Check if this validator is leader for a given slot.
        
        Args:
            slot: Slot to check (uses next slot if not provided)
            
        Returns:
            Leadership check result
        """
        if not self.is_initialized:
            return {
                "success": False,
                "message": "Safrole producer not initialized"
            }
        
        if slot is None:
            slot = self.producer.current_slot + 1
        
        try:
            is_leader = self.producer.is_leader_for_slot(slot)
            
            return {
                "success": True,
                "slot": slot,
                "is_leader": is_leader,
                "validator_index": self.producer.validator_index,
                "message": f"Validator {self.producer.validator_index} {'is' if is_leader else 'is not'} leader for slot {slot}"
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error checking leadership: {str(e)}",
                "error": str(e)
            }
    
    def produce_block(self, target_slot: int = None) -> Dict[str, Any]:
        """
        Produce a block for the specified slot.
        
        Args:
            target_slot: Slot to produce block for (uses next available if not provided)
            
        Returns:
            Block production result
        """
        if not self.is_initialized:
            return {
                "success": False,
                "message": "Safrole producer not initialized"
            }
        
        try:
            # If no target slot specified, find next leadership slot
            if target_slot is None:
                current_slot = self.producer.current_slot
                target_slot = None
                
                # Look for next leadership slot within reasonable range
                for slot in range(current_slot + 1, current_slot + 20):
                    if self.producer.is_leader_for_slot(slot):
                        target_slot = slot
                        break
                
                if target_slot is None:
                    return {
                        "success": False,
                        "message": "No leadership slots found in next 20 slots"
                    }
            
            # Check if we're leader for this slot
            if not self.producer.is_leader_for_slot(target_slot):
                return {
                    "success": False,
                    "message": f"Validator {self.producer.validator_index} is not leader for slot {target_slot}"
                }
            
            # Produce the block
            block = self.producer.produce_block(target_slot)
            
            if block:
                # Validate the block
                is_valid = self.producer.validate_block(block)
                
                return {
                    "success": True,
                    "message": f"Block produced successfully for slot {target_slot}",
                    "block": block,
                    "is_valid": is_valid,
                    "block_hash": block.get("block_hash"),
                    "slot": target_slot,
                    "work_reports_count": len(block.get("body", {}).get("work_reports", [])),
                    "preimages_count": len(block.get("body", {}).get("preimages", []))
                }
            else:
                return {
                    "success": False,
                    "message": f"Failed to produce block for slot {target_slot}"
                }
                
        except Exception as e:
            return {
                "success": False,
                "message": f"Error producing block: {str(e)}",
                "error": str(e)
            }
    
    def get_producer_status(self) -> Dict[str, Any]:
        """
        Get current status of the Safrole producer.
        
        Returns:
            Producer status information
        """
        if not self.is_initialized:
            return {
                "success": False,
                "message": "Safrole producer not initialized",
                "is_initialized": False
            }
        
        try:
            stats = self.producer.get_producer_stats()
            
            # Add additional status info
            next_leadership_slot = None
            current_slot = self.producer.current_slot
            
            for slot in range(current_slot + 1, current_slot + 20):
                if self.producer.is_leader_for_slot(slot):
                    next_leadership_slot = slot
                    break
            
            return {
                "success": True,
                "is_initialized": True,
                "producer_stats": stats,
                "next_leadership_slot": next_leadership_slot,
                "state_file_path": self.state_file_path
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error getting producer status: {str(e)}",
                "error": str(e)
            }
    
    def simulate_block_sequence(self, num_slots: int = 5) -> Dict[str, Any]:
        """
        Simulate block production for multiple slots.
        
        Args:
            num_slots: Number of slots to simulate
            
        Returns:
            Simulation results
        """
        if not self.is_initialized:
            return {
                "success": False,
                "message": "Safrole producer not initialized"
            }
        
        try:
            blocks = self.producer.simulate_block_production_sequence(num_slots)
            
            return {
                "success": True,
                "message": f"Simulated {num_slots} slots",
                "blocks_produced": len(blocks),
                "blocks": blocks,
                "simulation_stats": {
                    "total_slots": num_slots,
                    "blocks_produced": len(blocks),
                    "success_rate": len(blocks) / num_slots * 100
                }
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error in simulation: {str(e)}",
                "error": str(e)
            }


# Global integration instance
safrole_integration = SafroleServerIntegration()


# Convenience functions for server endpoints

def initialize_safrole(validator_index: int = 0) -> Dict[str, Any]:
    """Initialize Safrole producer for server use."""
    return safrole_integration.initialize_producer(validator_index)


def check_safrole_leadership(slot: int = None) -> Dict[str, Any]:
    """Check leadership for a slot."""
    return safrole_integration.check_leadership(slot)


def produce_safrole_block(target_slot: int = None) -> Dict[str, Any]:
    """Produce a block using Safrole."""
    return safrole_integration.produce_block(target_slot)


def get_safrole_status() -> Dict[str, Any]:
    """Get Safrole producer status."""
    return safrole_integration.get_producer_status()


def simulate_safrole_sequence(num_slots: int = 5) -> Dict[str, Any]:
    """Simulate Safrole block production sequence."""
    return safrole_integration.simulate_block_sequence(num_slots)


# Example usage and testing
if __name__ == "__main__":
    print("🔗 Safrole Server Integration Test")
    print("=" * 40)
    
    # Test initialization
    print("\n1. Initializing Safrole producer...")
    init_result = initialize_safrole(validator_index=0)
    print(f"   Result: {init_result['message']}")
    
    if init_result["success"]:
        # Test leadership check
        print("\n2. Checking leadership...")
        leadership_result = check_safrole_leadership()
        print(f"   Result: {leadership_result['message']}")
        
        # Test block production
        print("\n3. Attempting block production...")
        block_result = produce_safrole_block()
        print(f"   Result: {block_result['message']}")
        
        if block_result["success"]:
            print(f"   Block hash: {block_result['block_hash'][:32]}...")
            print(f"   Work reports: {block_result['work_reports_count']}")
            print(f"   Valid: {block_result['is_valid']}")
        
        # Test status
        print("\n4. Getting producer status...")
        status_result = get_safrole_status()
        if status_result["success"]:
            stats = status_result["producer_stats"]
            print(f"   Validator: {stats['validator_index']}")
            print(f"   Current slot: {stats['current_slot']}")
            print(f"   Blocks produced: {stats['blocks_produced']}")
            print(f"   Next leadership: {status_result['next_leadership_slot']}")
        
        # Test simulation
        print("\n5. Running simulation...")
        sim_result = simulate_safrole_sequence(3)
        if sim_result["success"]:
            sim_stats = sim_result["simulation_stats"]
            print(f"   Slots simulated: {sim_stats['total_slots']}")
            print(f"   Blocks produced: {sim_stats['blocks_produced']}")
            print(f"   Success rate: {sim_stats['success_rate']:.1f}%")
    
    print("\n✅ Integration test completed!")
    print("\n💡 Usage in server:")
    print("   from server.safrole_integration import *")
    print("   result = produce_safrole_block()")
