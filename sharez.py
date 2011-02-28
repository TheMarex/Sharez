#!/usr/bin/python

import os
import sys
import shlex
import subprocess
import re
import threading
import gtk
import urlparse, urllib
import time

# Magic make-things-work call -_-
gtk.gdk.threads_init()

DATADIR = "./"

class RsyncParser:
    _proc = None
    _job = None

    def __init__(self, proc, job):
        self._proc = proc
        self._job = job

    def _parse(self, data):
        exp = ".*\s([\d]{1,3})%\s.*"
        exp = re.compile(exp)
        out = exp.findall(data)
        if out:
            prog = int(out[0])
            self._job.update_progress(prog)

    def start(self):
        buff = ""
        out = self._proc.stdout.read(1)
        while out:
            if out == '\r' or out == '\n':
                self._parse(buff)
                buff = ""
            else:
                buff += out
            out = self._proc.stdout.read(1)

class Job(threading.Thread):
    _src = None
    _dst = None
    _manager = None
    _proc = None
    _killed = False

    def __init__(self, src, dst):
        self._src = src
        self._dst = dst

        threading.Thread.__init__(self)

    def get_src(self):
        return self._src

    def get_dst(self):
        return self._dst
    
    def run(self):
        cmd = "rsync -aP %s %s" % (self._src, self._dst)
        cmd = shlex.split(cmd)
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        parser = RsyncParser(self._proc, self)
        parser.start()
        if not self._killed:
            self._manager.job_finished(self)

    def cancel(self):
        self._killed = True
        self._proc.kill()

    def set_manager(self, manager):
        self._manager = manager

    def update_progress(self, percent):
        if self._manager:
            self._manager.update_progress(self, percent)

class Manager:
    _jobs = None
    _current = None
    _boss = None
    _min = 1

    def __init__(self, boss):
        self._jobs = []
        self._current = []
        self._boss = boss

    def cancel(self):
        for job in self._current:
            job.cancel()
        
        self._current = []
        self._jobs = []

    def update(self):
        if  len(self._current) < self._min\
        and len(self._jobs) > 0:
            self._start_job()

    def add_job(self, job):
        self._jobs.append(job)
        job.set_manager(self)
        self.update()

    def _start_job(self):
        job = self._jobs[0]
        self._jobs.remove(job)
        self._current.append(job)
        job.start()

    def job_finished(self, job):
        self._boss.job_finished(job)
        self._current.remove(job)
        self.update()

    def update_progress(self, job, percent):
        self._boss.update_progress(job, percent)

class DestList(gtk.TreeView):
    _store = None
    _dst = None
    _handler = None

    def __init__(self, dst, handler):
        gtk.TreeView.__init__(self)
        
        self._store = gtk.ListStore(str)
        self._dst = dst
        self._handler = handler

        cell = gtk.CellRendererText()
        name = os.path.basename(dst)
        col = gtk.TreeViewColumn(name, cell, text=0)
        self.append_column(col)
        self.set_model(self._store)

        sel = self.get_selection()
        sel.set_mode(gtk.SELECTION_MULTIPLE)

        self.enable_model_drag_dest([('text/plain', 0, 0)], gtk.gdk.ACTION_DEFAULT)
        self.connect('drag-data-received', self._drop)
        self.connect('key-release-event', self._key)

    def get_dest(self):
        return self._dst

    def get_src(self):
        it = self._store.get_iter_first()
        src = []
        while it:
            val = self._store.get_value(it, 0)
            src.append(val)
            it = self._store.iter_next(it)
        return src

    def remove_row(self, src):
        it = self._store.get_iter_first()
        while it:
            val = self._store.get_value(it, 0)
            if val == src:
                self._store.remove(it)
                return True
            it = self._store.iter_next(it)
        return False

    def _delete_current_row(self):
        model, rows = self.get_selection().get_selected_rows()
        iters = [model.get_iter(r) for r in rows]
        for it in iters:
            model.remove(it)

    def _key(self, widget, event):
        key = gtk.gdk.keyval_name(event.keyval)
        if key == "Delete":
            self._delete_current_row()

    def _drop(self, widget, context, x, y, sel, info, timestamp):
        path = urllib.unquote(urlparse.urlparse(sel.data.strip()).path)
        self._handler.dropped(self._dst, path)
        self._store.append((path,))

class DropLocation(gtk.Label):
    _handler = None

    def __init__(self, handler):
        gtk.Label.__init__(self)
        self.set_angle(90)
        self.set_size_request(30, -1)

        self._handler = handler

        self.set_text("Drop here.")
        self.drag_dest_set(gtk.DEST_DEFAULT_ALL, [], gtk.gdk.ACTION_DEFAULT | gtk.gdk.ACTION_COPY)
        self.drag_dest_add_uri_targets()
        self.drag_dest_add_text_targets()
        self.connect('drag-data-received', self._drop)

    def _drop(self, widget, context, x, y, sel, info, timestamp):
        path = urllib.unquote(urlparse.urlparse(sel.data.strip()).path)
        if os.path.exists(path):
            if os.path.isdir(path):
                context.finish(True, False, timestamp)
                self._handler.new_dest(path)
                return
        context.finish(False, False, timestamp)

class MainWin:
    _builder = None
    _store = None
    _box = None
    _button = None
    _lists = None
    _jobs = None
    _manager = None
    _is_running = False

    def __init__(self):
        self._builder = gtk.Builder()
        self._builder.add_from_file(os.path.join(DATADIR, "sharez.gtk"))
        self._builder.connect_signals(self)
        
        self._lists = {}
        self._jobs = []
        self._manager = Manager(self)

        self._button = self._builder.get_object("cancelButton")

        win = self._builder.get_object("mainWin")
        win.connect('destroy', self._close)
        win.set_size_request(800, 600)

        self._box = self._builder.get_object("destBox")
        drop = DropLocation(self)
        frame = gtk.Frame()
        frame.add(drop)
        self._box.pack_start(frame, False, True)

        self._store = self._builder.get_object("procStore")
        win.show_all()
    
    def dropped(self, dst, src):
        if self._is_running:
            job = Job(dst, src)
            self._add_job(job)

    def new_dest(self, path):
        l = DestList(path, self)
        self._lists[path] = l
        self._box.pack_start(l)
        self._box.show_all()
    
    def _close(self, widget):
        gtk.main_quit()

    def _cancel(self, widget):
        self._manager.cancel()
        self._jobs = []
        self._store.clear()
        self._button.set_sensitive(False)
        self._is_running = False

    def _add_job(self, job):
        src = job.get_src()
        dst = job.get_dst()
        self._store.append((src, dst, 0))
        self._jobs.append(job)
        self._manager.add_job(job)

    def _remove_job(self, job):
        it = self._get_iter(job)
        self._store.remove(it)
        self._jobs.remove(job)

        dst = job.get_dst()
        src = job.get_src()
        l = self._lists[dst]
        l.remove_row(src)

        if len(self._jobs) == 0:
            self._button.set_sensitive(False)
            self._is_running = False

    def _get_iter(self, job):
        it = self._store.get_iter_first()
        src = job.get_src()
        dst = job.get_dst()
        while it:
            rsrc, rdst = self._store.get(it, 0, 1)
            if src == rsrc and dst == rdst:
                return it
            it = self._store.next_iter()

        return None

    def job_finished(self, job):
        self._remove_job(job)

    def update_progress(self, job, prog):
        it = self._get_iter(job)
        self._store.set(it, 2, prog)

    def _run(self, widget):
        if self._is_running:
            return

        for dst in self._lists:
            l = self._lists[dst]
            for src in l.get_src():
                j = Job(src, dst)
                self._add_job(j)

        self._is_running = True
        self._button.set_sensitive(True)

win = MainWin()
gtk.main()
