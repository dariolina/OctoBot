#!/usr/bin/env python3
"""
Test script for External Signal Integration

This script tests the signal client and validates your setup.

Usage:
    python test_signal_integration.py
"""

import asyncio
import sys
import json
from pathlib import Path

# Add octobot to path
sys.path.insert(0, str(Path(__file__).parent))

from octobot.utils.signal_client import ExternalSignalClient, create_signal_client_from_config


def print_header(text):
    """Print a formatted header"""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60 + "\n")


def print_result(test_name, passed, details=""):
    """Print test result"""
    status = "✓ PASS" if passed else "✗ FAIL"
    print(f"{status} | {test_name}")
    if details:
        print(f"       {details}")


async def test_signal_client():
    """Test the signal client functionality"""
    print_header("Testing Signal Client")
    
    # Test 1: Create client
    try:
        client = ExternalSignalClient(
            url="http://localhost:8000/latest",
            freshness_seconds=600,
            timeout=10
        )
        print_result("Create client", True)
    except Exception as e:
        print_result("Create client", False, str(e))
        return False
    
    # Test 2: Fetch signal
    try:
        signal = await client.get_signal()
        if signal:
            print_result("Fetch signal", True, f"Got signal for {signal.get('symbol')}")
        else:
            print_result("Fetch signal", False, "No signal returned")
            return False
    except Exception as e:
        print_result("Fetch signal", False, str(e))
        return False
    
    # Test 3: Validate signal format
    try:
        required_fields = ["symbol", "timestamp", "action", "confidence", "tp_pct", "sl_pct"]
        missing_fields = [f for f in required_fields if f not in signal]
        
        if missing_fields:
            print_result("Validate format", False, f"Missing fields: {missing_fields}")
            return False
        else:
            print_result("Validate format", True)
    except Exception as e:
        print_result("Validate format", False, str(e))
        return False
    
    # Test 4: Check signal freshness
    try:
        is_fresh = client._is_signal_fresh(signal)
        print_result("Check freshness", is_fresh, 
                    "Signal is fresh" if is_fresh else "Signal is stale")
    except Exception as e:
        print_result("Check freshness", False, str(e))
        return False
    
    # Test 5: Check if actionable
    try:
        is_actionable = client.is_signal_actionable(signal)
        action = signal.get('action')
        print_result("Check actionable", True, 
                    f"Action: {action}, Actionable: {is_actionable}")
    except Exception as e:
        print_result("Check actionable", False, str(e))
        return False
    
    # Test 6: Display signal details
    print("\nSignal Details:")
    print("-" * 60)
    print(f"  Symbol:     {signal.get('symbol')}")
    print(f"  Timestamp:  {signal.get('timestamp')}")
    print(f"  Action:     {signal.get('action')}")
    print(f"  Confidence: {signal.get('confidence', 0):.2%}")
    print(f"  Reason:     {signal.get('reason', 'N/A')}")
    print(f"  TP:         {signal.get('tp_pct', 0):.2%}")
    print(f"  SL:         {signal.get('sl_pct', 0):.2%}")
    print("-" * 60)
    
    return True


async def test_config_loading():
    """Test loading signal client from config"""
    print_header("Testing Config Loading")
    
    # Test with sample config
    test_config = {
        "external_signal": {
            "enabled": True,
            "url": "http://localhost:8000/latest",
            "freshness_seconds": 600,
            "timeout": 10
        }
    }
    
    try:
        client = create_signal_client_from_config(test_config)
        if client:
            print_result("Load from config", True)
            
            # Test fetching with configured client
            signal = await client.get_signal()
            if signal:
                print_result("Fetch with config", True)
                return True
            else:
                print_result("Fetch with config", False, "No signal returned")
                return False
        else:
            print_result("Load from config", False, "Client not created")
            return False
    except Exception as e:
        print_result("Load from config", False, str(e))
        return False


async def test_endpoint_availability():
    """Test if the signal endpoint is available"""
    print_header("Testing Endpoint Availability")
    
    import aiohttp
    
    endpoints = [
        ("http://localhost:8000/latest", "Latest signal"),
        ("http://localhost:8000/health", "Health check"),
        ("http://localhost:8000/", "Root endpoint"),
    ]
    
    all_ok = True
    
    for url, name in endpoints:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        data = await response.json()
                        print_result(name, True, f"Status: {response.status}")
                    else:
                        print_result(name, False, f"Status: {response.status}")
                        all_ok = False
        except aiohttp.ClientError as e:
            print_result(name, False, f"Connection error: {e}")
            all_ok = False
        except Exception as e:
            print_result(name, False, str(e))
            all_ok = False
    
    return all_ok


async def test_signal_validation():
    """Test signal validation logic"""
    print_header("Testing Signal Validation")
    
    client = ExternalSignalClient(
        url="http://localhost:8000/latest",
        freshness_seconds=600
    )
    
    # Test valid signal
    valid_signal = {
        "symbol": "BTC-USDT",
        "timestamp": "2025-12-05T10:00:00Z",
        "action": "long",
        "confidence": 0.75,
        "tp_pct": 0.01,
        "sl_pct": 0.01
    }
    
    is_valid = client._validate_signal(valid_signal)
    print_result("Valid signal", is_valid)
    
    # Test invalid signals
    invalid_signals = [
        ({}, "Empty signal"),
        ({"symbol": "BTC-USDT"}, "Missing fields"),
        ({**valid_signal, "action": "invalid"}, "Invalid action"),
        ({**valid_signal, "confidence": "not_a_number"}, "Invalid confidence"),
    ]
    
    all_ok = True
    for invalid_signal, description in invalid_signals:
        is_valid = client._validate_signal(invalid_signal)
        if is_valid:
            print_result(f"Reject {description}", False, "Should be invalid")
            all_ok = False
        else:
            print_result(f"Reject {description}", True)
    
    return all_ok


def print_setup_instructions():
    """Print setup instructions if tests fail"""
    print("\n" + "=" * 60)
    print("  SETUP INSTRUCTIONS")
    print("=" * 60 + "\n")
    
    print("If tests failed, make sure you have:")
    print()
    print("1. Started the signal backend:")
    print("   python octobot/external_signals/example_backend.py")
    print()
    print("2. Or started your own backend that returns:")
    print("   {")
    print('     "symbol": "BTC-USDT",')
    print('     "timestamp": "2025-12-05T10:00:00Z",')
    print('     "action": "long",')
    print('     "confidence": 0.75,')
    print('     "tp_pct": 0.01,')
    print('     "sl_pct": 0.01')
    print("   }")
    print()
    print("3. Test the endpoint manually:")
    print("   curl http://localhost:8000/latest")
    print()


async def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("  EXTERNAL SIGNAL INTEGRATION TEST SUITE")
    print("=" * 60)
    
    results = []
    
    # Test endpoint availability first
    endpoint_ok = await test_endpoint_availability()
    results.append(("Endpoint Availability", endpoint_ok))
    
    if not endpoint_ok:
        print("\n⚠ Warning: Signal endpoint is not available!")
        print("  Some tests will fail. Please start the backend first.")
        print_setup_instructions()
        return
    
    # Run other tests
    client_ok = await test_signal_client()
    results.append(("Signal Client", client_ok))
    
    config_ok = await test_config_loading()
    results.append(("Config Loading", config_ok))
    
    validation_ok = await test_signal_validation()
    results.append(("Signal Validation", validation_ok))
    
    # Print summary
    print_header("TEST SUMMARY")
    
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    
    for test_name, ok in results:
        status = "✓ PASS" if ok else "✗ FAIL"
        print(f"{status} | {test_name}")
    
    print()
    print(f"Total: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Your external signal integration is ready.")
        print()
        print("Next steps:")
        print("1. Update your config.json with external_signal settings")
        print("2. Replace example_backend.py logic with your AI agent swarm")
        print("3. Test with paper trading first")
        print("4. Monitor logs and trades")
    else:
        print("\n⚠ Some tests failed. Please review the errors above.")
        print_setup_instructions()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()

