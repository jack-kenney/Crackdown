"""Inspect Windows output devices and Crackdown sessions without changing settings."""
import argparse
import json
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, help='Inspect this process instead of all Crackdown processes')
    parser.add_argument('--seconds', type=float, default=2, help='Meter sampling duration (0.1 to 30 seconds)')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This diagnostic requires Windows')
    if not 0.1 <= args.seconds <= 30:
        parser.error('--seconds must be between 0.1 and 30')
    if args.pid is not None and args.pid <= 0:
        parser.error('--pid must be positive')
    try:
        import psutil
        from pycaw.utils import AudioUtilities, AudioSession
        from pycaw.api.audiopolicy import IAudioSessionControl2
        from pycaw.api.endpointvolume import IAudioMeterInformation
        from pycaw.constants import EDataFlow, DEVICE_STATE
    except ImportError:
        parser.exit(2, 'Install diagnostic dependencies with: python -m pip install pycaw psutil\n')

    processes = {}
    for process in psutil.process_iter(['pid', 'name', 'exe']):
        if ((args.pid is not None and process.pid == args.pid) or
                (args.pid is None and (process.info['name'] or '').lower() == 'crackdown.exe')):
            processes[process.pid] = process.info['exe']
    if not processes:
        parser.exit(1, 'No matching running game process. Launch the game first.\n')

    try:
        default = AudioUtilities.GetSpeakers().id
    except Exception:
        default = None
    report = {'processes': processes, 'devices': []}
    meters = []
    for device in AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value):
        result = {'name': device.FriendlyName, 'id': device.id,
                  'default_multimedia': device.id == default, 'sessions': []}
        report['devices'].append(result)
        try:
            result['volume_percent'] = device.volume_percent
            result['muted'] = bool(device.EndpointVolume.GetMute())
            enumerator = device.AudioSessionManager.GetSessionEnumerator()
            for index in range(enumerator.GetCount()):
                control = enumerator.GetSession(index).QueryInterface(IAudioSessionControl2)
                session = AudioSession(control)
                if session.ProcessId not in processes:
                    continue
                entry = {'pid': session.ProcessId, 'state': session.State,
                         'volume_percent': 100 * session.SimpleAudioVolume.GetMasterVolume(),
                         'muted': bool(session.SimpleAudioVolume.GetMute()), 'peak': 0.0,
                         'nonzero_samples': 0, 'samples': 0}
                result['sessions'].append(entry)
                try:
                    meters.append((control.QueryInterface(IAudioMeterInformation), entry))
                except Exception as exc:
                    entry['meter_error'] = str(exc)
        except Exception as exc:
            result['error'] = str(exc)

    deadline = time.monotonic() + args.seconds
    while meters and time.monotonic() < deadline:
        for meter, entry in meters[:]:
            try:
                peak = meter.GetPeakValue()
                entry['peak'] = max(entry['peak'], peak)
                entry['nonzero_samples'] += peak > 0
                entry['samples'] += 1
            except Exception as exc:
                entry['meter_error'] = str(exc)
                meters.remove((meter, entry))
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
