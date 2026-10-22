"""Trusted document broker: file dialogs/saves outside, PDF operations inside AppContainer."""
from __future__ import annotations

from pathlib import Path
from .user_paths import user_path, same_file
import logging
import queue
import subprocess
import threading
import unicodedata
import time
from uuid import uuid4

from .errors import PDFOperationError, PDFPasswordRequired, PDFSecurityError
from .private_work import PrivateDirectory, check_budget
from .security import copy_checked,check_pdf
from .docx_export import atomic_copy
from .sandbox import sandbox_command
from . import rpc_codec
from .provenance import read_zone


class BrokerEditor:
    def __init__(self,source,password=None):
        self.source=user_path(source)
        self.output_path=self.source
        self._zone=read_zone(self.source)
        if not self.source.is_file() or self.source.suffix.lower()!='.pdf':
            raise PDFOperationError('Seleziona un file PDF esistente.')
        self._temp=PrivateDirectory()
        self._root=Path(self._temp.name)
        self._original=self._root/'original.pdf'
        self._password=password
        self._process=None
        self._lock=threading.RLock()
        self._state=None
        self._saved_revision=None
        self._closed=False
        try:
            copy_checked(self.source,self._original)
            # Preserve the independent 12-second input gate before starting the
            # persistent editor (whose individual operations have a 30s budget).
            check_pdf(self._original,password)
            self._start()
            self._saved_revision=self.path
        except Exception:
            self.close()
            raise

    def _start(self):
        restore=self._state
        if restore:
            # A crash after committing an undo branch can leave old redo files
            # removed before the new state was acknowledged. Keep the selected
            # accepted revision and prune only unavailable history entries.
            selected=restore['revisions'][restore['index']]
            revisions=[]
            for path in restore['revisions']:
                try:
                    self.checked_path(path)
                    if path.is_file():revisions.append(path)
                except FileNotFoundError:pass
            if selected not in revisions:raise PDFOperationError('La revisione selezionata non è disponibile per il recupero.')
            restore={**restore,'revisions':revisions,'index':revisions.index(selected)}
        command=sandbox_command('rpc',self._root,timeout=3600000,output_limit=0)
        self._process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            cwd=self._root,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self._exchange(dict(method='open',source=self._original,password=self._password,restore=restore),30)

    def _exchange(self,request,timeout=30):
        process=self._process
        started=time.monotonic()
        received=queue.Queue(maxsize=1)
        def receive():
            try:
                received.put((True,rpc_codec.read(process.stdout)))
            except Exception as exc:
                received.put((False,exc))
        thread=threading.Thread(target=receive,daemon=True)
        thread.start()
        deadline=time.monotonic()+timeout
        watchdog=threading.Timer(timeout,lambda:process.kill() if process.poll() is None else None)
        watchdog.daemon=True
        watchdog.start()
        try:
            process.stdin.write(rpc_codec.dumps(request))
            process.stdin.flush()
            valid,response=received.get(timeout=max(.01,deadline-time.monotonic()))
            if not valid:
                raise response
            if not isinstance(response,dict) or type(response.get('ok'))is not bool:
                raise ValueError('Invalid worker envelope')
            if not response['ok']:
                message=response.get('error','PDF operation failed')
                if not isinstance(message,str) or len(message)>4096:
                    raise ValueError('Invalid worker error')
                message=''.join(ch for ch in message if ch in '\n\t' or not unicodedata.category(ch).startswith('C')).replace('<','‹').replace('>','›')
                error={'PDFPasswordRequired':PDFPasswordRequired,'PDFSecurityError':PDFSecurityError}.get(response.get('type'),PDFOperationError)
                raise error(message)
            state=response.get('state')
            if not isinstance(state,dict) or set(state)!= {'revisions','index','encrypted','document_info','security_report'}:
                raise ValueError('Invalid worker state')
            paths=state['revisions']
            if not isinstance(paths,list) or not 1<=len(paths)<=1000 or type(state['index'])is not int or not 0<=state['index']<len(paths):
                raise ValueError('Invalid revision history')
            for path in paths:
                self.checked_path(path)
            if type(state['encrypted'])is not bool:
                raise ValueError('Invalid encryption flag')
            from .document_info import DocumentInfo
            from .security import SecurityReport
            if not isinstance(state['document_info'],DocumentInfo) or not isinstance(state['security_report'],SecurityReport):
                raise ValueError('Invalid document description')
            from .rpc_validation import result
            value=result(request['method'],response.get('value'))
            self._state=state
            return value
        except (PDFOperationError,):
            raise
        except Exception as exc:
            # Preserve the supervisor's exit status before _stop() can kill it.
            # Never log request arguments: they may contain passwords/PDF text.
            exit_code=process.poll()
            if isinstance(exc,EOFError) and exit_code is None:
                try:exit_code=process.wait(timeout=.2)
                except subprocess.TimeoutExpired:pass
            logging.getLogger(__name__).error(
                'PDF worker transport failed: method=%s pid=%s exit_code=%s elapsed=%.3fs budget=%ss error=%s',
                request.get('method'),process.pid,exit_code,time.monotonic()-started,timeout,type(exc).__name__)
            self._stop()
            raise PDFOperationError('Processo PDF interrotto o risposta non valida. La revisione precedente è conservata.') from exc
        finally:
            watchdog.cancel()

    def checked_path(self,path):
        if not isinstance(path,Path) or not path.is_absolute():
            raise ValueError('Invalid artifact path')
        target=path.resolve()
        if not target.is_relative_to(self._root):
            raise ValueError('Artifact outside document workspace')
        current=path
        while current!=self._root:
            info=current.lstat()
            if current.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Artifact reparse point')
            current=current.parent
        return target

    def call(self,method,*args):
        with self._lock:
            if self._closed:
                raise PDFOperationError('Documento chiuso.')
            if self._process is None or self._process.poll() is not None:
                self._stop()
                self._start()
            return self._exchange(dict(method=method,args=list(args)))

    def __getattr__(self,name):
        from .rpc_worker import METHODS
        if name in METHODS:
            return lambda *args:self.call(name,*args)
        raise AttributeError(name)

    @property
    def path(self):return self._state['revisions'][self._state['index']]
    @property
    def dirty(self):return self.path!=self._saved_revision
    @property
    def modified_from_original(self):return self._state['index']!=0
    @property
    def can_undo(self):return self._state['index']>0
    @property
    def can_redo(self):return self._state['index']<len(self._state['revisions'])-1
    @property
    def encrypted(self):return self._state['encrypted']
    @property
    def document_info(self):return self._state['document_info']
    @property
    def security_report(self):return self._state['security_report']

    def insert_pdf(self,incoming,after):
        snapshot=self._root/(uuid4().hex+'.pdf')
        try:
            copy_checked(Path(incoming),snapshot)
            zone=read_zone(Path(incoming))
            result=self.call('insert_pdf',snapshot,after)
            self._zone=max((z for z in (zone,self._zone) if z is not None),default=None)
            return result
        finally:snapshot.unlink(missing_ok=True)

    def validate_form_design(self,proposals,deleted):
        return self.call('validate_form_design',proposals,deleted)

    def apply_ocr(self,result,revision):
        snapshot=self._root/(uuid4().hex+'.pdf')
        try:
            copy_checked(Path(result),snapshot)
            return self.call('apply_ocr',snapshot,revision)
        finally:snapshot.unlink(missing_ok=True)

    def extract(self,pages,destination):
        destination=user_path(destination)
        if any(same_file(destination,path) for path in [self.source,self.output_path,*self._state['revisions']]):
            raise PDFOperationError('Scegli un altro nome per la copia estratta.')
        artifact=self._root/(uuid4().hex+'.pdf')
        try:
            self.call('extract',pages,artifact)
            self.checked_path(artifact)
            atomic_copy(artifact,destination,artifact_root=self._root,zone=self._zone)
            return destination
        finally:artifact.unlink(missing_ok=True)

    def save_as(self,destination):
        if self.encrypted:
            raise PDFOperationError('I PDF protetti non possono essere risalvati con questa versione.')
        destination=user_path(destination)
        if destination.suffix.lower()!='.pdf' or not destination.parent.is_dir() or destination.resolve().is_relative_to(self._root):
            raise PDFOperationError('Scegli un file PDF esterno alla cartella di lavoro.')
        self.checked_path(self.path)
        check_budget(self._root)
        atomic_copy(self.path,destination,artifact_root=self._root,zone=self._zone)
        self.output_path=destination
        self._saved_revision=self.path
        return destination

    def split(self,groups,directory,*,progress=None,cancelled=None):
        """Export the accepted revision into a new directory via sandboxed extract.

        Only broker-generated names are published. No existing file is replaced;
        cancellation/failure removes just the files created by this operation.
        """
        from .pdf_split import validate_groups,part_name
        from .localization import tr
        directory=user_path(directory)
        if not directory.is_dir() or directory.resolve().is_relative_to(self._root):
            raise PDFOperationError(tr('Scegli una cartella di destinazione esistente.'))
        with self._lock:
            if self.encrypted:
                raise PDFOperationError(tr('I PDF protetti sono in sola lettura.'))
            groups=validate_groups(groups,self.metadata().page_count)
            if cancelled and cancelled():return []
            # Fixed folder and filenames avoid long, reserved or hostile source names.
            folder=directory/'PDF_split'
            suffix=1
            while True:
                try:
                    folder.mkdir()
                    break
                except FileExistsError:
                    suffix+=1
                    folder=directory/f'PDF_split_{suffix}'
            outputs=[]
            complete=False
            try:
                for index,pages in enumerate(groups,1):
                    if cancelled and cancelled():return []
                    output=folder/part_name(index)
                    # Track even a failed publication so cleanup is complete.
                    outputs.append(output)
                    self.extract(pages,output)
                    if progress:progress(index)
                if cancelled and cancelled():return []
                complete=True
                return outputs
            except OSError as exc:
                raise PDFOperationError(tr('Salvataggio non riuscito: {0}',exc)) from exc
            finally:
                if not complete:
                    for output in outputs:
                        output.unlink(missing_ok=True)
                    folder.rmdir()

    def _stop(self,graceful=False):
        process,self._process=self._process,None
        if process:
            if graceful and self._lock.acquire(blocking=False):
                try:
                    if process.stdin:
                        process.stdin.close()
                    process.wait(timeout=1)
                except (OSError,subprocess.TimeoutExpired):pass
                finally:self._lock.release()
            if process.poll() is None:
                process.kill()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:pass
            for stream in (process.stdin,process.stdout):
                if stream:
                    try:stream.close()
                    except OSError:pass

    def close(self):
        self._closed=True
        self._stop(graceful=True)
        self._password=None
        self._temp.cleanup()
