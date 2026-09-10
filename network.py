"""NetworkManager interface"""
import asyncio
import os
import pty
import re
import shutil
import subprocess
import sys

try:
    import dbus
    HAS_DBUS = True
except ImportError:
    HAS_DBUS = False

def get_wifi_interface():
    """Auto-detect WiFi interface"""
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'DEVICE,TYPE', 'device'],
                               capture_output=True, text=True, check=True)
        for line in result.stdout.strip().split('\n'):
            if ':wifi' in line:
                return line.split(':')[0]
    except:
        pass
    return 'wlan0'

def get_wifi_list():
    """Get available WiFi networks"""
    try:
        result = subprocess.run(
            ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY,IN-USE', 'device', 'wifi', 'list'],
            capture_output=True, text=True, check=True)
        
        networks, seen = [], set()
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split(':')
            if len(parts) >= 4 and parts[0] and parts[0] not in seen:
                seen.add(parts[0])
                networks.append({
                    'ssid': parts[0],
                    'signal': int(parts[1]) if parts[1] else 0,
                    'security': parts[2],
                    'connected': parts[3] == '*'
                })
        return sorted(networks, key=lambda x: x['signal'], reverse=True)
    except:
        return []

def get_current_connection():
    """Get active connection name"""
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'NAME', 'connection', 'show', '--active'],
                               capture_output=True, text=True, check=True)
        return result.stdout.strip().split('\n')[0] or None
    except:
        return None

def get_station_info():
    """Get station status"""
    try:
        current = get_current_connection()
        info = {'state': 'connected' if current else 'disconnected', 
                'scanning': 'false', 'frequency': '-', 'security': '-'}
        
        if current:
            result = subprocess.run(['nmcli', '-t', '-f', 'ACTIVE,SSID,FREQ,SECURITY', 
                                    'device', 'wifi', 'list'], capture_output=True, text=True)
            for line in result.stdout.strip().split('\n'):
                if line.startswith('yes:') or line.startswith('*:'):
                    parts = line.split(':')
                    if len(parts) >= 4:
                        info['frequency'] = parts[2] or '-'
                        info['security'] = parts[3] or '-'
                    break
        return info
    except:
        return {'state': 'disconnected', 'scanning': 'false', 'frequency': '-', 'security': '-'}

def connect_wifi(ssid, password, hidden=False):
    """Connect to WiFi (supports hidden SSIDs)"""
    try:
        cmd = ['nmcli', 'device', 'wifi', 'connect', ssid]
        if password:
            cmd.extend(['password', password])
        if hidden:
            cmd.append('hidden')
            cmd.append('yes')
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # If connection failed, delete the connection profile that was created
        if result.returncode != 0:
            subprocess.run(['nmcli', 'connection', 'delete', ssid], 
                          capture_output=True, text=True)
        
        return result.returncode == 0, result.stderr or result.stdout
    except Exception as e:
        return False, str(e)

def connect_802_1x(ssid, username, password, eap_method="peap", phase2_auth="mschapv2", hidden=False):
    """Connect to 802.1X enterprise WiFi (supports hidden SSIDs)
    
    Supports:
    - EAP: peap, ttls, tls
    - Phase2: mschapv2, mschap, pap, chap, gtc, md5
    - Hidden SSID networks
    """
    try:
        iface = get_wifi_interface()
        subprocess.run(['nmcli', 'connection', 'delete', ssid], capture_output=True)
        
        # Build command based on EAP method
        cmd = [
            'nmcli', 'connection', 'add', 'type', 'wifi', 'con-name', ssid,
            'ifname', iface, 'ssid', ssid, 'wifi-sec.key-mgmt', 'wpa-eap',
            '802-1x.eap', eap_method.lower(), '802-1x.identity', username
        ]
        
        # Add hidden SSID support
        if hidden:
            cmd.extend(['wifi.hidden', 'yes'])
        
        # Add auth-specific parameters
        if eap_method.lower() in ['peap', 'ttls']:
            # PEAP and TTLS use phase2 auth + password
            cmd.extend(['802-1x.phase2-auth', phase2_auth.lower()])
            cmd.extend(['802-1x.password', password])
        elif eap_method.lower() == 'tls':
            # TLS uses certificates (for now, treat password as private key password)
            cmd.extend(['802-1x.private-key-password', password])
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return False, result.stderr
        
        result = subprocess.run(['nmcli', 'connection', 'up', ssid],
                               capture_output=True, text=True)
        
        # If connection failed, delete the connection profile that was created
        if result.returncode != 0:
            subprocess.run(['nmcli', 'connection', 'delete', ssid], 
                          capture_output=True, text=True)
        
        return result.returncode == 0, result.stderr or "Connected"
    except Exception as e:
        return False, str(e)

def forget_network(ssid):
    """Delete saved WiFi network by SSID"""
    try:
        result = subprocess.run(['nmcli', 'connection', 'delete', ssid],
                               capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

def disconnect():
    """Disconnect from network"""
    try:
        result = subprocess.run(['nmcli', 'device', 'disconnect', get_wifi_interface()],
                               capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

def wifi_enabled():
    """Check if WiFi is enabled"""
    if HAS_DBUS:
        try:
            bus = dbus.SystemBus()
            nm = bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            props = dbus.Interface(nm, "org.freedesktop.DBus.Properties")
            sw = bool(props.Get("org.freedesktop.NetworkManager", "WirelessEnabled"))
            hw = bool(props.Get("org.freedesktop.NetworkManager", "WirelessHardwareEnabled"))
            return sw and hw
        except Exception:
            pass
            
    try:
        result = subprocess.run(['nmcli', 'radio', 'wifi'], capture_output=True, text=True)
        return result.stdout.strip() == 'enabled'
    except:
        return True

def toggle_wifi():
    """Toggle WiFi on/off"""
    if HAS_DBUS:
        try:
            bus = dbus.SystemBus()
            nm = bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            props = dbus.Interface(nm, "org.freedesktop.DBus.Properties")
            current = bool(props.Get("org.freedesktop.NetworkManager", "WirelessEnabled"))
            props.Set("org.freedesktop.NetworkManager", "WirelessEnabled", not current)
            return not current
        except Exception as e:
            with open("/tmp/gazelle_debug.log", "a") as f:
                f.write(f"WiFi Toggle DBus Error: {e}\n")

    try:
        enabled = wifi_enabled()
        subprocess.run(['nmcli', 'radio', 'wifi', 'off' if enabled else 'on'])
        return not enabled
    except:
        return wifi_enabled()

def wwan_enabled():
    """Check if WWAN is enabled"""
    if HAS_DBUS:
        try:
            bus = dbus.SystemBus()
            nm = bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            props = dbus.Interface(nm, "org.freedesktop.DBus.Properties")
            sw = bool(props.Get("org.freedesktop.NetworkManager", "WwanEnabled"))
            hw = bool(props.Get("org.freedesktop.NetworkManager", "WwanHardwareEnabled"))
            return sw and hw
        except Exception:
            pass
            
    try:
        result = subprocess.run(['nmcli', 'radio', 'wwan'], capture_output=True, text=True)
        return result.stdout.strip() == 'enabled'
    except:
        return True

def toggle_wwan():
    """Toggle WWAN on/off"""
    if HAS_DBUS:
        try:
            bus = dbus.SystemBus()
            nm = bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            props = dbus.Interface(nm, "org.freedesktop.DBus.Properties")
            current = bool(props.Get("org.freedesktop.NetworkManager", "WwanEnabled"))
            props.Set("org.freedesktop.NetworkManager", "WwanEnabled", not current)
            return not current
        except Exception as e:
            with open("/tmp/gazelle_debug.log", "a") as f:
                f.write(f"WWAN Toggle DBus Error: {e}\n")

    try:
        enabled = wwan_enabled()
        subprocess.run(['nmcli', 'radio', 'wwan', 'off' if enabled else 'on'])
        return not enabled
    except:
        return wwan_enabled()

def get_ethernet_interface():
    """Auto-detect Ethernet interface"""
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'DEVICE,TYPE', 'device'],
                               capture_output=True, text=True, check=True)
        for line in result.stdout.strip().split('\n'):
            if ':ethernet' in line:
                return line.split(':')[0]
    except:
        pass
    return None

def connect_802_1x_wired(con_name, username, password, eap_method="peap", phase2_auth="mschapv2"):
    """Connect to 802.1X enterprise wired network

    Supports:
    - EAP: peap, ttls, tls
    - Phase2: mschapv2, mschap, pap, chap, gtc, md5
    """
    try:
        iface = get_ethernet_interface()
        if not iface:
            return False, "No Ethernet interface found"

        subprocess.run(['nmcli', 'connection', 'delete', con_name], capture_output=True)

        # Build command for wired 802.1X
        cmd = [
            'nmcli', 'connection', 'add', 'type', '802-3-ethernet', 'con-name', con_name,
            'ifname', iface, '802-1x.eap', eap_method.lower(), '802-1x.identity', username
        ]

        # Add auth-specific parameters
        if eap_method.lower() in ['peap', 'ttls']:
            cmd.extend(['802-1x.phase2-auth', phase2_auth.lower()])
            cmd.extend(['802-1x.password', password])
        elif eap_method.lower() == 'tls':
            cmd.extend(['802-1x.private-key-password', password])

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            return False, result.stderr

        result = subprocess.run(['nmcli', 'connection', 'up', con_name],
                               capture_output=True, text=True)

        # If connection failed, delete the connection profile that was created
        if result.returncode != 0:
            subprocess.run(['nmcli', 'connection', 'delete', con_name],
                          capture_output=True, text=True)

        return result.returncode == 0, result.stderr or "Connected"
    except Exception as e:
        return False, str(e)

def disconnect_ethernet():
    """Disconnect from wired network"""
    try:
        iface = get_ethernet_interface()
        if not iface:
            return False
        result = subprocess.run(['nmcli', 'device', 'disconnect', iface],
                               capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

def is_enterprise(security):
    """Check if network is 802.1X"""
    return 'WPA-EAP' in security or '802.1X' in security

def is_owe(security):
    """Check if network uses OWE (Enhanced Open / WPA3-OWE)"""
    return 'OWE' in security or 'WPA3-OWE' in security

# ---------------------------------------------------------------------------
# VPN connector: async wrapper around `nmcli connection up <name> --ask`
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(rb'\x1b\[[0-9;?]*[A-Za-z]')


def _strip_ansi(data: bytes) -> bytes:
    return _ANSI_RE.sub(b'', data)


def _classify_prompt(last_line: str) -> str:
    """Best-effort classification of a prompt line (used by UI to pick widget)."""
    low = last_line.lower()
    if 'group' in low and '[' in low and ']' in low:
        return 'group'
    if 'password' in low or 'пароль' in low:
        return 'password'
    if 'username' in low or 'login' in low or 'user:' in low:
        return 'username'
    if 'response' in low:
        return 'response'
    return 'generic'


def _detect_prompt(buf: bytes):
    """Return (end_pos, kind, last_line) if buf's last non-empty line ends with ':'.

    Structural rule only — idle-state is guaranteed by the caller.
    """
    nl = buf.rfind(b'\n')
    tail = buf[nl + 1:] if nl != -1 else buf
    tail_start = nl + 1

    stripped = tail.rstrip(b' \t\r')
    if not stripped or not stripped.endswith(b':'):
        return None, None, None

    pos = tail_start + len(stripped)
    try:
        last_line = stripped.decode('utf-8', errors='replace')
    except Exception:
        last_line = repr(stripped)
    return pos, _classify_prompt(last_line), last_line


def _extract_group_options(last_line: str):
    m = re.search(r'\[([^\]]+)\]', last_line)
    if not m:
        return []
    return [opt.strip() for opt in m.group(1).split('|') if opt.strip()]

class VPNConnector:
    """Async wrapper around `nmcli connection up <name> --ask` via PTY.

    Each prompt is delegated to an async callback supplied by the caller:

        async def on_prompt(block_text, kind, last_line, options) -> str | None:
            # return answer string, or None to cancel

        connector = VPNConnector()
        ok, msg = await connector.connect("M1", on_prompt)
    """

    IDLE_TIMEOUT = 0.3        # seconds of silence => prompt is ready
    PROMPT_TIMEOUT = 300.0    # seconds to wait for the user's answer
    OVERALL_TIMEOUT = 900.0   # seconds for the whole operation

    async def connect(self, name, on_prompt):
        if not shutil.which('nmcli'):
            return False, "nmcli not found in PATH"

        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as e:
            return False, f"Failed to open PTY: {e}"

        env = os.environ.copy()

        try:
            proc = await asyncio.create_subprocess_exec(
                'nmcli', 'connection', 'up', name, '--ask',
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                env=env,
            )
        except Exception as e:
            os.close(master_fd)
            os.close(slave_fd)
            return False, f"Failed to spawn nmcli: {e}"

        os.close(slave_fd)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_readable():
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                data = b''
            queue.put_nowait(data)

        loop.add_reader(master_fd, on_readable)

        clean_buf = b''
        last_answered_offset = 0
        answered_positions = set()
        cancelled = False

        try:
            overall_deadline = loop.time() + self.OVERALL_TIMEOUT

            while True:
                if loop.time() > overall_deadline:
                    cancelled = True
                    return False, "Operation timed out"

                try:
                    chunk = await asyncio.wait_for(
                        queue.get(), timeout=self.IDLE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    chunk = None

                if chunk == b'':
                    break  # EOF
                if chunk is not None:
                    clean_buf += _strip_ansi(chunk)
                    continue

                pos, kind, last_line = _detect_prompt(clean_buf)
                if pos is None or pos in answered_positions:
                    continue

                answered_positions.add(pos)
                block = clean_buf[last_answered_offset:pos]
                block_text = block.decode('utf-8', errors='replace')
                options = _extract_group_options(last_line) if kind == 'group' else []

                try:
                    answer = await asyncio.wait_for(
                        on_prompt(block_text, kind, last_line, options),
                        timeout=self.PROMPT_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    cancelled = True
                    break
                except Exception as e:
                    cancelled = True
                    return False, f"Prompt handler failed: {e}"

                if answer is None:
                    cancelled = True
                    break

                os.write(master_fd, (answer + '\n').encode('utf-8'))
                last_answered_offset = pos

            if cancelled:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                return False, "Cancelled"

            rc = await proc.wait()
            tail_text = clean_buf[last_answered_offset:].decode(
                'utf-8', errors='replace'
            ).strip()

            if rc == 0:
                return True, "Connected"
            return False, self._extract_error(tail_text) or f"nmcli exit {rc}"

        finally:
            loop.remove_reader(master_fd)
            try:
                os.close(master_fd)
            except OSError:
                pass
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()

    @staticmethod
    def _extract_error(text: str) -> str:
        for line in reversed(text.split('\n')):
            s = line.strip()
            if s.startswith('Error:'):
                return s
        for line in reversed(text.split('\n')):
            s = line.strip()
            if s:
                return s
        return ""


# ---------------------------------------------------------------------------

def get_vpn_list():
    """Get all VPN connections configured in NetworkManager"""
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show'],
                               capture_output=True, text=True, check=True)
        active_vpn = get_active_vpn()
        vpns = []
        
        # VPN types supported by NetworkManager
        vpn_types = [':vpn', ':wireguard', ':vpnc', ':pptp', ':openconnect', ':openvpn']
        
        for line in result.stdout.strip().split('\n'):
            # Check if line contains any VPN type
            if any(vpn_type in line for vpn_type in vpn_types):
                name = line.split(':')[0]
                vpns.append({
                    'name': name,
                    'active': name in active_vpn
                })
        return sorted(vpns, key=lambda x: (not x['active'], x['name']))
    except:
        return []

def get_active_vpn():
    """Get currently active VPN connection name"""
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show', '--active'],
                               capture_output=True, text=True, check=True)
        
        # VPN types supported by NetworkManager
        vpn_types = [':vpn', ':wireguard', ':vpnc', ':pptp', ':openconnect', ':openvpn']
        
        active = set()
        for line in result.stdout.strip().split('\n'):
            if any(vpn_type in line for vpn_type in vpn_types):
                active.add(line.split(':')[0])
        return active
    except:
        return set()

def connect_vpn(name):
    """Connect to VPN by name"""
    try:
        result = subprocess.run(['nmcli', 'connection', 'up', name],
                               capture_output=True, text=True)
        return result.returncode == 0, result.stderr or result.stdout
    except Exception as e:
        return False, str(e)

def disconnect_vpn(name):
    """Disconnect VPN by name"""
    try:
        result = subprocess.run(['nmcli', 'connection', 'down', name],
                               capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

def get_modem_info():
    """Get modem information via ModemManager"""
    try:
        # Get modem list
        result = subprocess.run(['mmcli', '--list-modems'],
                               capture_output=True, text=True, check=True)

        # Extract modem ID from output (e.g., "/org/freedesktop/ModemManager1/Modem/2")
        modem_id = None
        for line in result.stdout.strip().split('\n'):
            if '/Modem/' in line:
                modem_id = line.split('/Modem/')[-1].split()[0]
                break

        if not modem_id:
            return None

        # Get detailed modem info
        result = subprocess.run(['mmcli', '-m', modem_id],
                               capture_output=True, text=True, check=True)

        info = {'signal': '0%', 'operator': '-', 'tech': '-', 'state': 'unknown'}

        for line in result.stdout.split('\n'):
            line = line.strip()
            if 'signal quality:' in line:
                # Extract percentage (e.g., "48% (cached)")
                info['signal'] = line.split(':')[1].strip().split()[0]
            elif 'operator name:' in line:
                info['operator'] = line.split(':')[1].strip()
            elif 'access tech:' in line:
                info['tech'] = line.split(':')[1].strip().upper()
            elif 'state:' in line:
                # Extract state, removing ANSI color codes
                state = line.split(':')[1].strip()
                # Remove ANSI codes like [32mconnected[0m
                state = state.replace('[32m', '').replace('[0m', '')
                info['state'] = state

        return info
    except:
        return None

def get_wwan_list():
    """Get all WWAN (cellular) connections configured in NetworkManager"""
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show'],
                               capture_output=True, text=True, check=True)
        active_wwan = get_active_wwan()
        modem_info = get_modem_info()

        wwans = []
        for line in result.stdout.strip().split('\n'):
            if ':gsm' in line:
                name = line.split(':')[0]
                wwan_entry = {
                    'name': name,
                    'active': name in active_wwan
                }

                # Add modem info if connection is active
                if wwan_entry['active'] and modem_info:
                    wwan_entry['signal'] = modem_info['signal']
                    wwan_entry['operator'] = modem_info['operator']
                    wwan_entry['tech'] = modem_info['tech']
                else:
                    wwan_entry['signal'] = '-'
                    wwan_entry['operator'] = '-'
                    wwan_entry['tech'] = '-'

                wwans.append(wwan_entry)

        return sorted(wwans, key=lambda x: (not x['active'], x['name']))
    except:
        return []

def get_active_wwan():
    """Get currently active WWAN connection name"""
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show', '--active'],
                               capture_output=True, text=True, check=True)

        active = set()
        for line in result.stdout.strip().split('\n'):
            if ':gsm' in line:
                active.add(line.split(':')[0])
        return active
    except:
        return set()

def connect_wwan(name):
    """Connect to WWAN by name"""
    try:
        result = subprocess.run(['nmcli', 'connection', 'up', name],
                               capture_output=True, text=True)
        return result.returncode == 0, result.stderr or result.stdout
    except Exception as e:
        return False, str(e)

def disconnect_wwan(name):
    """Disconnect WWAN by name"""
    try:
        result = subprocess.run(['nmcli', 'connection', 'down', name],
                               capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False
