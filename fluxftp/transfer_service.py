"""Recursive file-tree transfer orchestration across local and remote sites."""
import ftplib
import os
import posixpath
import re
from pathlib import Path

from .connection_model import Cancelled, Entry, safe_name
from .transfer_options import TransferOptions
from .verification_reports import tracked_file
from .xdupe import guard as xdupe_guard


@tracked_file
@xdupe_guard
def transfer_tree(source, destination, src, dst, is_dir, cancel, progress, resume=False, depth=0, options=None, skipped=None, renamed=None, planned=None, listed_size=None, skipped_directories=None, conflict=None, notice=None, file_started=None):
    """Copy between local/remote or two FTP servers without following directory links."""
    if cancel.is_set():
        raise Cancelled("Cancelled")
    if depth > 100:
        raise ValueError("Directory depth limit exceeded")
    options = options or (destination.transfer_options if destination else source.transfer_options if source else TransferOptions())
    name = posixpath.basename(src.rstrip("/")) if source else Path(src).name
    size = listed_size
    if options.skip_enabled and options.skip_zero and not is_dir and size is None:
        try:
            size = source.size(src) if source else Path(src).stat().st_size
        except ftplib.error_perm:
            # Servers without SIZE can still transfer; unknown size is not zero.
            size = None
    reason = options.skip_reason(name, is_dir, size, automatic=depth > 0)
    if reason:
        if is_dir and skipped_directories is not None:
            skipped_directories.add(src)
        if skipped:
            skipped(src, reason)
        return False
    if not is_dir:
        if name.lower().endswith('.sfv') and (options.single_sfv or options.skip_multiple_sfv):
            from .crc_check import sfv_names
            parent = (posixpath.dirname(src) or '/') if source else Path(src).parent
            siblings = source.list(parent) if source else [Entry(p.name, p.is_dir(), is_link=p.is_symlink()) for p in parent.iterdir()]
            if name not in sfv_names(siblings, options):
                if skipped: skipped(src, 'Misc: SFV selection rule')
                return False
        if planned is not None:
            planned.append((src, dst, size))
            return True
        if file_started:
            file_started(src, dst)
        if options.exists_enabled and not (destination and options.atomic_upload):
            from .file_exists import transfer_existing
            return transfer_existing(source, destination, src, dst, cancel, progress, resume, options, skipped, renamed, conflict, notice)
        if options.atomic_upload or options.hash_verification != "Off":
            from .transfer_reliability import transfer
            return transfer(source, destination, src, dst, cancel, progress, resume, options, notice)
        if source and destination:
            from .protocol_adapter import connection_capabilities
            source_caps, destination_caps = connection_capabilities(source), connection_capabilities(destination)
            if 'cloud' in (source_caps.backend, destination_caps.backend):
                from .cloud_connection import relay
                # Object stores do not support appending at an arbitrary byte offset.
                if resume and any(e.name == posixpath.basename(dst) for e in destination.list(posixpath.dirname(dst) or '/')):
                    raise ValueError('Cloud relay cannot resume an existing destination; choose overwrite or enable resume-failure overwrite.')
                relay(source, destination, src, dst, cancel, progress, resume=False)
            elif source_caps.direct_fxp and destination_caps.direct_fxp:
                from .core import fxp
                fxp(source, destination, src, dst, resume=resume, cancel=cancel, progress=progress)
            elif 'unknown' not in (source_caps.backend, destination_caps.backend):
                from .remote_relay import relay
                relay(source, destination, src, dst, cancel, progress, resume)
            else:
                raise ValueError('The selected backend has no remote-to-remote transfer adapter.')
        elif destination:
            from .protocol_adapter import adapter_for
            adapter_for(destination).transfer(src, dst, True, cancel, progress, resume)
        elif source:
            from .protocol_adapter import adapter_for
            adapter_for(source).transfer(dst, src, False, cancel, progress, resume)
        else:
            source_path, destination_path = Path(src), Path(dst)
            if source_path.resolve() == destination_path.resolve():
                raise ValueError("Source and destination are the same file")
            if destination_path.is_symlink():
                raise ValueError("Refusing to overwrite a symbolic link")
            partial = destination_path.with_name(destination_path.name + ".fluxpart")
            if partial.is_symlink():
                raise ValueError("Partial copy cannot be a symbolic link")
            total = source_path.stat().st_size
            offset = partial.stat().st_size if resume and partial.exists() else 0
            if offset > total:
                raise ValueError("Partial copy exceeds source size")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            options.check_space(dst, total - offset)
            with source_path.open("rb") as input_file, partial.open("ab" if offset else "wb") as output_file:
                input_file.seek(offset)
                while block := input_file.read(262144):
                    if cancel.is_set():
                        raise Cancelled("Cancelled")
                    options.check_space(dst, len(block))
                    output_file.write(block)
                    offset += len(block)
                    progress(offset, total)
            if offset != total:
                from .verification_reports import evidence
                evidence('size', 'mismatch', total, offset)
                raise IOError(f"Size mismatch: expected {total}, copied {offset}")
            from .verification_reports import evidence
            evidence('size', 'verified', total, offset)
            if cancel.is_set():
                raise Cancelled("Cancelled")
            partial.replace(destination_path)
            import shutil
            shutil.copystat(source_path, destination_path)
            progress(total, total)
        return True
    if source:
        entries = source.list(src)
    else:
        entries = [Entry(p.name, p.is_dir(), p.stat().st_size, is_link=p.is_symlink()) for p in Path(src).iterdir()]
    if source and not entries and re.search(r'-\s*COMPLETE\s*-', name, re.IGNORECASE):
        if skipped_directories is not None:
            skipped_directories.add(src)
        if skipped:
            skipped(src, 'Empty COMPLETE marker folder')
        return False
    reason = options.skip_reason(name, True, automatic=depth > 0, empty=not entries)
    if reason:
        if skipped_directories is not None:
            skipped_directories.add(src)
        if skipped:
            skipped(src, reason)
        return False
    kept = []
    for entry in entries:
        reason = options.skip_reason(entry.name, entry.is_dir, automatic=True)
        if reason:
            if entry.is_dir and skipped_directories is not None:
                skipped_directories.add(posixpath.join(src, entry.name) if source else str(Path(src) / entry.name))
            if skipped:
                skipped(posixpath.join(src, entry.name) if source else str(Path(src) / entry.name), reason)
        else:
            kept.append(entry)
    entries = kept
    created = False
    if destination:
        parent = posixpath.dirname(dst.rstrip("/")) or "/"
        name = posixpath.basename(dst.rstrip("/"))
        matches = [e for e in destination.list(parent) if e.name == name]
        if matches:
            if not matches[0].is_dir or matches[0].is_link:
                raise ValueError(f"Destination is not a directory: {dst}")
        else:
            destination.mkdir(dst)
            created = True
    else:
        if Path(dst).is_symlink():
            raise ValueError("Refusing to write through a directory symlink")
        created = not Path(dst).exists()
        if created and options.deny_parent_name and Path(dst).name.casefold() == Path(dst).parent.name.casefold():
            raise ValueError('New directory cannot have the same name as its parent')
        Path(dst).mkdir(parents=True, exist_ok=True)
    if destination and options.remove_missing_zero:
        from .misc_transfer import remove_missing_zero
        remove_missing_zero(source, destination, src, dst, options, cancel)
    route = "FXP" if source and destination else "Upload" if destination else "Download" if source else "Copy"
    entries.sort(key=lambda entry: options.priority(entry.name))
    converted_names = [options.filename(e.name, route) for e in entries]
    compared = [name.casefold() for name in converted_names] if not destination and os.name == "nt" else converted_names
    if len(compared) != len(set(compared)):
        raise ValueError("Filename conversion would overwrite another file in this folder.")
    planned_before = len(planned) if planned is not None else 0
    for e, target_name in zip(entries, converted_names):
        safe_name(e.name)
        safe_name(target_name)
        if e.is_link:
            raise ValueError(f"Symbolic links are not transferred: {e.name}")
        s = posixpath.join(src, e.name) if source else str(Path(src) / e.name)
        d = posixpath.join(dst, target_name) if destination else str(Path(dst) / target_name)
        transfer_tree(source, destination, s, d, e.is_dir, cancel, progress, resume, depth + 1, options, skipped, renamed, planned, e.size, skipped_directories, conflict, notice, file_started)
    if created and options.no_empty_dirs and (planned is None or len(planned) == planned_before):
        if destination:
            if not destination.list(dst): destination.remove(dst, is_dir=True)
        elif not any(Path(dst).iterdir()):
            Path(dst).rmdir()
    return True
