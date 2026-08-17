"""
data/users.py

Prototype-only user roster shown on the admin-only "Users" tab
(users_page.UsersPage).

Moved out of the old flat data.py as part of splitting it into a data/
package (see PROGRESS.md, Phase 2e). See data/accounts.py's docstring
(Phase 2a) for why this directory has no __init__.py yet -- `import data`
still resolves to the original data.py unchanged until Phase 2f's cutover.
"""

USERS = [
    {"name": "John Doe", "email": "john@gmail.com", "phone": "0917 123 4567",
     "role": "User", "credits": 5600, "spent": 2450, "joined": "Jul 25, 2026",
     "last_active": "Jul 31, 2026 03:45 PM", "status": "Active"},
    {"name": "Jane Smith", "email": "jane@gmail.com", "phone": "0917 234 5678",
     "role": "User", "credits": 3200, "spent": 1100, "joined": "Jul 24, 2026",
     "last_active": "Jul 31, 2026 11:20 AM", "status": "Active"},
    {"name": "Mike Brown", "email": "mike@gmail.com", "phone": "0917 345 6789",
     "role": "User", "credits": 1500, "spent": 500, "joined": "Jul 23, 2026",
     "last_active": "Jul 30, 2026 08:30 PM", "status": "Active"},
    {"name": "Anna Lee", "email": "anna@gmail.com", "phone": "0917 456 7890",
     "role": "User", "credits": 8800, "spent": 6200, "joined": "Jul 22, 2026",
     "last_active": "Jul 31, 2026 01:05 PM", "status": "Active"},
    {"name": "Mark Reyes", "email": "mark@gmail.com", "phone": "0917 567 8901",
     "role": "Admin", "credits": 12000, "spent": 4000, "joined": "Jul 20, 2026",
     "last_active": "Jul 31, 2026 09:15 AM", "status": "Active"},
    {"name": "Chris Evans", "email": "chris@gmail.com", "phone": "0917 678 9012",
     "role": "User", "credits": 600, "spent": 300, "joined": "Jul 19, 2026",
     "last_active": "Jul 28, 2026 07:10 PM", "status": "Inactive"},
    {"name": "Pat Garcia", "email": "pat@gmail.com", "phone": "0917 789 0123",
     "role": "User", "credits": 0, "spent": 0, "joined": "Jul 18, 2026",
     "last_active": "Jul 25, 2026 02:30 PM", "status": "Suspended"},
    {"name": "Luis Lane", "email": "luis@gmail.com", "phone": "0917 890 1234",
     "role": "User", "credits": 2400, "spent": 1500, "joined": "Jul 17, 2026",
     "last_active": "Jul 31, 2026 12:00 PM", "status": "Active"},
    {"name": "Kate Williams", "email": "kate@gmail.com", "phone": "0917 901 2345",
     "role": "User", "credits": 3750, "spent": 2200, "joined": "Jul 16, 2026",
     "last_active": "Jul 29, 2026 06:40 PM", "status": "Inactive"},
    {"name": "Daniel Bennett", "email": "daniel@gmail.com", "phone": "0917 012 3456",
     "role": "Admin", "credits": 9100, "spent": 2100, "joined": "Jul 15, 2026",
     "last_active": "Jul 31, 2026 02:20 PM", "status": "Active"},
]
