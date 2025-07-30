
# REALAND A-F011 FINGERPRINT DEVICE SETUP GUIDE
**For HR Payroll System Integration**

---

## 📋 REQUIRED EQUIPMENT

- **REALAND A-F011 Fingerprint Device**
- **Ethernet Cable (Cat5e or Cat6)**
- **Network Switch/Router with available ports**
- **Server/Computer running Django HR System**
- **Power adapter for REALAND device**

---

## 🔌 PHYSICAL SETUP REQUIREMENTS

### Hardware Connection Chain:
```
REALAND A-F011 Device → Ethernet Cable → Network Switch/Router → Server/Computer (Django App)
```

### Network Configuration Requirements:
- **Device IP**: 192.168.1.100 (static IP)
- **Server IP**: 192.168.1.50 (same network)
- **Communication Port**: 4370 (REALAND default)
- **Protocol**: TCP/IP

---

## ⚙️ STEP-BY-STEP INSTALLATION

### **STEP 1: Physical Connection**
1. **Connect power** to REALAND A-F011 device
2. **Connect Ethernet cable** from device to network switch/router
3. **Power on** the REALAND device
4. **Wait for device boot** (approximately 30-60 seconds)

### **STEP 2: Device Network Configuration**
1. **Access device menu** (use device keypad/touchscreen)
2. **Navigate to**: Menu → System → Network Settings
3. **Configure network settings**:
   - IP Address: `192.168.1.100`
   - Subnet Mask: `255.255.255.0`
   - Gateway: `192.168.1.1` (your router IP)
   - DNS: `8.8.8.8`
4. **Set communication port**: `4370`
5. **Enable TCP/IP communication**
6. **Save settings** and restart device

### **STEP 3: Network Verification**
**From your Django server computer, test connection**:
1. **Open Command Prompt/Terminal**
2. **Test device connectivity**:
   ```
   ping 192.168.1.100
   ```
   *(Should receive successful ping responses)*
3. **Test port connectivity**:
   ```
   telnet 192.168.1.100 4370
   ```
   *(Should connect successfully)*

### **STEP 4: Django System Configuration**
1. **Access Django Admin Panel**
2. **Navigate to**: Attendance → Attendance Devices
3. **Add New Device** with these settings:
   - Device Name: "Main Office Entrance"
   - Device Type: "REALAND A-F011"
   - IP Address: `192.168.1.100`
   - Port: `4370`
   - Location: "Main Entrance"
   - Status: "Active"
4. **Test Connection** using the "Test Connection" button

---

## 🔄 HOW IT WORKS (REAL-TIME PROCESS)

### **Employee Attendance Process**:
1. **Employee approaches** REALAND device
2. **Places finger** on fingerprint scanner
3. **Device recognizes** fingerprint instantly
4. **Device records** attendance with timestamp
5. **Django system automatically** connects to device every 5 minutes
6. **System retrieves** new attendance logs
7. **Attendance records** appear in Django admin immediately
8. **HR staff can view** real-time attendance data

---

## 🌐 NETWORK ARCHITECTURE OVERVIEW

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  REALAND A-F011 │    │ Network Switch  │    │  Django Server  │
│  192.168.1.100  │◄──►│  192.168.1.1    │◄──►│  192.168.1.50   │
│  Port: 4370     │    │  Router/Switch  │    │  Port: 8000     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

---

## 🔒 SECURITY RECOMMENDATIONS

### **Network Security**:
- **Use private network** (not public internet)
- **Configure firewall** to allow only Django server access to port 4370
- **Set device admin password** on REALAND device
- **Use VPN** if connecting remotely

### **Device Security**:
- **Change default passwords** on REALAND device
- **Enable device access logs**
- **Regular firmware updates**
- **Physical security** - mount device securely

---

## ✅ VERIFICATION CHECKLIST

**Before going live, ensure**:
- [ ] Device powers on and displays ready status
- [ ] Network ping test successful
- [ ] Port 4370 connection test successful  
- [ ] Django admin shows device as "Connected"
- [ ] Test fingerprint enrollment works
- [ ] Test attendance logging works
- [ ] Real-time sync working (check every 5 minutes)

---

## 🚨 TROUBLESHOOTING

**If device not connecting**:
1. **Check network cable** connections
2. **Verify IP address** settings on device
3. **Check firewall** settings on server
4. **Restart device** and try again
5. **Contact IT support** if issues persist

**If attendance not syncing**:
1. **Check Django admin** → Attendance Devices → Connection Status
2. **Verify device time** matches server time
3. **Check network connectivity**
4. **Review Django logs** for error messages

---

## 📞 SUPPORT INFORMATION

**For technical support**:
- **Django System**: Contact development team
- **REALAND Device**: Refer to device manual or manufacturer support
- **Network Issues**: Contact IT administrator

---

**🎯 RESULT**: Once setup is complete, employees can use fingerprint authentication for instant attendance recording, and HR staff will see real-time attendance data in the Django admin panel automatically!