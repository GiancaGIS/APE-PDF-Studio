"""Real OS denial probes; uses only synthetic files, with a live network control."""
import sys,shutil,subprocess,socket
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ape_pdf_studio.core.private_work import PrivateDirectory
root=Path(__file__).resolve().parents[1]
public=root/'.dev-deps/sandbox-probe-runtime';public.mkdir(parents=True,exist_ok=True)
probe=public/'APEPDFSandboxProbe.exe'
shutil.copyfile(root/'build/windows/sandbox/APEPDFSandboxProbe.exe',probe)
with PrivateDirectory() as work, PrivateDirectory() as private:
    secret=Path(private)/'private.txt';secret.write_text('synthetic private probe')
    server=socket.socket();server.bind(('127.0.0.1',0));server.listen()
    control=socket.create_connection(server.getsockname(),timeout=1)
    accepted,_=server.accept();accepted.close();control.close()
    command=[str((root/'build/windows/sandbox/APEPDFSandbox.exe')),work,'10000','65536','1',str(public),str(probe),work,str(secret),str(server.getsockname()[1])]
    r=subprocess.run(command,input=b'',stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    print('Sandbox probe exit:',r.returncode)
    print(r.stdout.decode(errors='replace'))
    print(r.stderr.decode(errors='replace'))
    assert r.returncode==0
    server.settimeout(.1)
    try:
        escaped,_=server.accept();escaped.close();raise AssertionError('Sandbox reached the loopback listener')
    except TimeoutError:pass
    finally:server.close()
