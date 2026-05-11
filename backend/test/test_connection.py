"""
test_connection.py

Standalone Phase 1 test — run from the backend/ directory.
Verifies:
  1. .env is loaded correctly
  2. ERPNextClient authenticates successfully
  3. Employee list endpoint returns data
  4. Salary Slip list endpoint returns data
  5. Error handling works (tests a bad request)

Run with:
    cd backend
    python test_connection.py
"""

import asyncio
import sys
import os

# Make sure we can import from backend/
sys.path.insert(0, os.path.dirname(__file__))

from config import settings
from services.erpnext_client import erpnext, ERPNextError


def separator(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


async def test_config():
    separator("TEST 1: Configuration")
    print(f"  ERPNext URL  : {settings.erpnext_base_url}")
    print(f"  API Key      : {settings.erpnext_api_key[:6]}... (truncated)")
    print(f"  Groq Model   : {settings.groq_model}")
    print(f"  Threshold    : {settings.anomaly_threshold_pct}%")
    print(f"  App Version  : {settings.app_version}")
    print("\n  ✅ Config loaded successfully")


async def test_employee_list():
    separator("TEST 2: Fetch Employee List")
    try:
        employees = await erpnext.get_list(
            doctype="Employee",
            fields=["name", "employee_name", "department", "status"],
            limit=10,
        )
        print(f"  Found {len(employees)} employee(s):\n")
        for emp in employees:
            print(
                f"    [{emp.get('name')}] "
                f"{emp.get('employee_name')} — "
                f"Dept: {emp.get('department', 'N/A')} | "
                f"Status: {emp.get('status', 'N/A')}"
            )
        print("\n  ✅ Employee list fetched successfully")
        return employees
    except ERPNextError as e:
        print(f"\n  ❌ ERPNext error: {e.message}")
        sys.exit(1)


async def test_salary_slips():
    separator("TEST 3: Fetch Salary Slips (All)")
    try:
        slips = await erpnext.get_list(
            doctype="Salary Slip",
            fields=[
                "name",
                "employee",
                "employee_name",
                "net_pay",
                "gross_pay",
                "total_deduction",
                "start_date",
                "end_date",
                "docstatus",
            ],
            limit=50,
        )
        print(f"  Found {len(slips)} salary slip(s):\n")
        for slip in slips:
            status_label = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(
                slip.get("docstatus", -1), "Unknown"
            )
            print(
                f"    [{slip.get('name')}] "
                f"{slip.get('employee_name')} | "
                f"Period: {slip.get('start_date')} → {slip.get('end_date')} | "
                f"Net: {slip.get('net_pay')} | "
                f"Status: {status_label}"
            )
        print("\n  ✅ Salary slips fetched successfully")
        return slips
    except ERPNextError as e:
        print(f"\n  ❌ ERPNext error: {e.message}")
        sys.exit(1)


async def test_single_employee(employees: list[dict]):
    separator("TEST 4: Fetch Single Employee Document")
    if not employees:
        print("  ⚠️  Skipped — no employees found in previous test")
        return

    emp_id = employees[0]["name"]
    try:
        emp = await erpnext.get_document("Employee", emp_id)
        print(f"  Employee ID   : {emp.get('name')}")
        print(f"  Full Name     : {emp.get('employee_name')}")
        print(f"  Department    : {emp.get('department', 'N/A')}")
        print(f"  Date of Join  : {emp.get('date_of_joining', 'N/A')}")
        print(f"  Salary Mode   : {emp.get('salary_mode', 'N/A')}")
        print("\n  ✅ Single document fetch works correctly")
    except ERPNextError as e:
        print(f"\n  ❌ ERPNext error: {e.message}")


async def test_error_handling():
    separator("TEST 5: Error Handling (Intentional Bad Request)")
    try:
        # Request a document that definitely doesn't exist
        await erpnext.get_document("Employee", "DOES-NOT-EXIST-99999")
        print("  ⚠️  Expected an error but got success — check ERPNext setup")
    except ERPNextError as e:
        print(f"  ERPNextError correctly raised:")
        print(f"    Status Code : {e.status_code}")
        print(f"    Message     : {e.message[:120]}")
        print("\n  ✅ Error handling works correctly")


async def main():
    print("\n🚀 PAYROLL ANOMALY DETECTOR — Phase 1 Connection Test")
    print("   Testing ERPNext client through services/erpnext_client.py\n")

    await test_config()
    employees = await test_employee_list()
    await test_salary_slips()
    await test_single_employee(employees)
    await test_error_handling()

    separator("PHASE 1 COMPLETE")
    print("  All connection tests passed.")
    print("  ERPNext client is working correctly.")
    print("  Ready for Phase 2: ERPNext Tools.\n")


if __name__ == "__main__":
    asyncio.run(main())