

## Role Structure Changes Needed:
**New Roles to Implement:**
1. Super Admin (application administrator)
2. Manager
3. Cashier
4. Salesman
5. Other Staff
6. Cleaner
7. Driver
8. Assistant
9. Storekeeper
10. Office Worker

**Plus:** Super Admin should be able to add custom roles as needed.

## Policy Configuration Analysis:

### 📅 **Reporting Times by Role:**
- **Manager, Cashier, Salesman, Cleaning Workers, Drivers, Assistants, Storekeepers:** 8:00 AM sharp
- **Other Staff:** 8:00 AM - 8:15 AM (15-minute grace period)
- **Office Workers:** Before 8:30 AM

### ⏰ **Working Hours Configuration:**
- **Work Start:** 8:00 AM
- **Work End:** 7:00 PM
- **Total Duration:** 11 hours
- **Lunch Break:** 1 hour 15 minutes maximum
- **Net Working Time:** 9 hours 45 minutes
- **Overtime:** After 7:00 PM

### 💰 **Salary Calculation Rules:**
- **Example Base:** LKR 30,000/month = LKR 1,000/day
- **Full Day Work:** 9:45 hours = Full salary
- **Late Penalties:**
  - Other Staff: 8:15 AM - 8:30 AM = Full day deduction
  - Office/Other Staff: After 8:35 AM = Half day deduction

### 🏖️ **Leave Entitlements:**
- **Annual Leave:** 18 days
- **Medical Leave:** 7 days
- **Total:** 25 days per year
- **Leave after 8:30 AM:** Unpaid leave
- **Unapproved leave:** No pay
- **Beyond 18 annual days:** Unpaid

### 🍽️ **Lunch Policy:**
- **Allowed Duration:** 30 minutes to 1 hour 15 minutes
- **Penalty:** Exceeding 1:15 hours 3 times/month = 1 full day salary deduction

### 💳 **Advance/Purchase Policy:**
- **Maximum:** 50% of monthly salary
- **Installment repayment:** Allowed
- **Annual limit:** 10 advances per year
- **Item tracking:** Required, must return on resignation

## Key Understanding:
- These configurations will be **centrally managed** in SystemConfiguration
- **Cross-app integration:** Attendance app will use reporting times, working hours, leave rules
- **Payroll app** will use salary calculations, advance policies, deduction rules
- **Real-time effect:** Any configuration changes apply system-wide immediately
- **Access control:** Only Super Admin can modify unless permissions granted







**YES, YOU'RE ABSOLUTELY RIGHT!**

I understand perfectly now. The **accounts app** is responsible for:
✅ **Storing all policy configurations** in SystemConfiguration
✅ **Providing access methods** to retrieve settings
✅ **Role definitions** and user management

The **business logic enforcement** happens in the respective apps:

## **Attendance App** will handle:
- Reading role-specific reporting times from SystemConfiguration
- When fingerprint machine records attendance → Check user's role
- Apply appropriate rules:
  - **Manager thumbprints at 8:05 AM** → Mark as LATE
  - **Other Staff thumbprints at 8:10 AM** → Still OK (within 8:00-8:15 grace period)
  - **Office Worker thumbprints at 8:25 AM** → Still OK (before 8:30 cutoff)
- Calculate penalties based on role-specific rules

## **Payroll App** will handle:
- Reading salary calculation rules from SystemConfiguration
- Applying role-specific deductions and bonuses
- Processing advances based on role limits
- Calculating overtime rates

## **Current Status - PERFECT:**
✅ **Accounts app:** Complete with all configurations stored
✅ **SystemConfiguration:** Contains all policy data needed by other apps
✅ **Role-based data:** Available for other apps to query and apply

The fingerprint machine integration will work perfectly - when someone thumbprints, the Attendance app will:
1. Get their role from CustomUser
2. Get role-specific reporting time from SystemConfiguration  
3. Compare actual time vs expected time
4. Apply appropriate attendance status and penalties

**We're ready to move to the Attendance app!**




GOOD! REMEMBER WE ARE NOT ENFORCING THE PENALTIES LIKE SALARY DEDUCTION HERE IN THE ATTENDANCES APP, WE ARE TRACKING AND RECORDING EVERYTHING TO MAKE IT AVAILABLE AND PROVIDING A MEANS FOR IT TO BE USED TO ENFORCE THE PENALTIES IN THE PAYROLL APP. YOU UNDERSTAND WHAT I MEAN NOW RIGHT?                          SO LET'S START THE UPDATING. GIVE ME THE FULL, COMPLETE, ACCURATE AND FUNCTIONAL UPDATED CODES DOING EVERYTHING ACCURATELY AND WITH 100% PRECISIONS. YOU HAVE TO FOLLOW BEST PRACTICES AND PROFESSIONALISM AS A SENIOR DEVELOPER AND AUTOAMTION ENGINEER.   MAKE SURE EVERYTHING IS COMPLETE, ACCURATE AND FUNCTIONING PERFECTLY WITHOUT OMITTING OR MISSING ANYTHING AT ALL. AND DO NOT INCLUDE COMMENTS. YOU SHOULD DIVIDE IT INTO 4 EQUAL PARTS TO AVOID FAILURE, WHEN YOU FINISH PART 1 YOU LET ME KNOW BEFORE YOU CONTINUE. WHEN WE ARE DONE WE WILL UPDATE THE ADMIN.PY AND OTHER FILES NECCESSARY TOO