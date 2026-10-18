"""Shared literal types."""
from typing import Literal

UserType = Literal["admin", "principal", "sub_admin", "coordinator", "driver", "teacher", "student"]
StaffType = Literal["admin", "principal", "sub_admin", "coordinator", "driver", "teacher"]
PaymentMethod = Literal["cash", "card", "upi", "netbanking", "wallet"]

# Upper bound for any password input: protects the (deliberately slow) hasher.
PASSWORD_MAX_LENGTH = 128
