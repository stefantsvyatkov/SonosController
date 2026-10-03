"""Native accessible editors with explicit field ordering and value mappings."""

import wx

from .controls import Slider, numbers
from .i18n import _
from .menus import alarm_mode_supported, parse_alarm
from .util import valid_url

LABEL_WIDTH = 150
GAP = 8


class FieldGrid:
    """A two-column label/control grid whose rows can be shown and hidden.

    Hiding both cells of a row collapses it, so the dialog stays tidy, and the
    fields that remain keep their labels aligned in one column.
    """

    def __init__(self, parent):
        self.parent = parent
        self.sizer = wx.FlexGridSizer(cols=2, vgap=GAP, hgap=12)
        self.sizer.AddGrowableCol(1, 1)
        self.rows = {}
        self.controls = {}

    def add(self, key, label, factory):
        caption = wx.StaticText(self.parent, label=label)
        caption.SetMinSize((LABEL_WIDTH, -1))
        control = factory()
        control.SetName(label)
        self.sizer.Add(caption, 0, wx.ALIGN_CENTER_VERTICAL)
        self.sizer.Add(control, 1, wx.EXPAND)
        self.rows[key] = (caption, control)
        self.controls[key] = control
        return control

    def add_wide(self, key, control):
        """A control that carries its own label, such as a check box."""
        self.sizer.AddSpacer(0)
        self.sizer.Add(control, 0, wx.EXPAND)
        self.rows[key] = (None, control)
        self.controls[key] = control
        return control

    def show(self, key, visible):
        """Show or hide a row.  Returns True when the visibility changed."""
        caption, control = self.rows[key]
        if control.IsShown() == bool(visible):
            return False
        for window in (caption, control):
            if window is not None:
                window.Show(visible)
        return True


class AlarmDialog(wx.Dialog):
    # A one-off alarm is the safe default: it cannot keep going off unnoticed.
    RECURRENCES = ("ONCE", "DAILY", "WEEKDAYS", "WEEKENDS", "CUSTOM")
    PLAY_MODES = ("NORMAL", "SHUFFLE_NOREPEAT", "SHUFFLE", "REPEAT_ALL",
                  "REPEAT_ONE", "SHUFFLE_REPEAT_ONE")
    DAYS = None  # filled in at construction so the names are translated

    def __init__(self, parent, context):
        super().__init__(parent, title=_("Sonos alarm"),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.context, self.result = context, None
        alarm = context["alarm"]
        self.panel = panel = wx.Panel(self)
        self.grid = grid = FieldGrid(panel)
        self.controls = grid.controls

        self.DAYS = [_("Sunday"), _("Monday"), _("Tuesday"), _("Wednesday"),
                     _("Thursday"), _("Friday"), _("Saturday")]

        start = alarm.start_time if alarm else None
        hours = grid.add("timeHour", _("Hour"), lambda: numbers(panel, 24))
        minutes = grid.add("timeMinute", _("Minute"), lambda: numbers(panel, 60))
        hours.SetSelection(start.hour if start else 7)
        minutes.SetSelection(start.minute if start else 0)

        room = grid.add("room", _("Room"), lambda: wx.Choice(panel, choices=context["roomNames"]))
        uid = alarm.room_uuid if alarm else context["defaultRoom"]
        room.SetSelection(context["roomIds"].index(uid) if uid in context["roomIds"] else wx.NOT_FOUND)

        recurrence = alarm.recurrence if alarm else "ONCE"
        repeat = grid.add("recurrence", _("Repeat"), lambda: wx.Choice(panel, choices=[
            _("Once"), _("Every day"), _("Weekdays"), _("Weekends"), _("Selected days")]))
        repeat.SetSelection(self.RECURRENCES.index(recurrence)
                            if recurrence in self.RECURRENCES else 4)

        days = grid.add("days", _("Days"),
                        lambda: wx.CheckListBox(panel, choices=self.DAYS, size=(-1, 130)))
        for index in range(7):
            days.Check(index, recurrence.startswith("ON_") and str(index) in recurrence[3:])

        grid.add("volume", _("Volume (%)"),
                 lambda: Slider(panel, value=alarm.volume if alarm else 20,
                                minValue=0, maxValue=100,
                                style=wx.SL_HORIZONTAL | wx.SL_LABELS))

        for key, label, value in (
                ("enabled", _("Enabled"), alarm.enabled if alarm else True),
                ("linked", _("Include grouped rooms"),
                 alarm.include_linked_zones if alarm else False)):
            control = wx.CheckBox(panel, label=label)
            control.SetValue(value)
            grid.add_wide(key, control)

        self.sourceIds = ([0] if alarm else []) + [1, 2] + list(range(3, 3 + len(context["favorites"])))
        labels = ([_("Current sound (unchanged)")] if alarm else []) \
            + [_("Sonos chime"), _("Audio URL")] + context["favoriteNames"]
        source = grid.add("source", _("Sound"), lambda: wx.Choice(panel, choices=labels))
        sourceId = 1
        if alarm and alarm.program_uri:
            sourceId = 0
            for index, favorite in enumerate(context["favorites"]):
                if favorite.reference.resources[0].uri == alarm.program_uri:
                    sourceId = index + 3
                    break
            if sourceId == 0 and valid_url(alarm.program_uri):
                sourceId = 2
        source.SetSelection(self.sourceIds.index(sourceId))

        grid.add("url", _("Audio URL"), lambda: wx.TextCtrl(
            panel, value=alarm.program_uri if alarm and valid_url(alarm.program_uri) else ""))

        duration = grid.add("durationMode", _("Duration"),
                            lambda: wx.Choice(panel, choices=[_("Unlimited"), _("Custom")]))
        duration.SetSelection(1 if alarm and alarm.duration else 0)
        durationHours = grid.add("durationHour", _("Duration hour"),
                                 lambda: numbers(panel, 24))
        durationMinutes = grid.add("durationMinute", _("Duration minute"),
                                   lambda: numbers(panel, 60))
        durationHours.SetSelection(alarm.duration.hour if alarm and alarm.duration else 1)
        durationMinutes.SetSelection(alarm.duration.minute if alarm and alarm.duration else 0)

        mode = grid.add("play_mode", _("Play mode"), lambda: wx.Choice(panel, choices=[
            _("Normal"), _("Shuffle"), _("Shuffle and repeat all"), _("Repeat all"),
            _("Repeat one"), _("Shuffle and repeat one")]))
        mode.SetSelection(self.PLAY_MODES.index(alarm.play_mode)
                          if alarm and alarm.play_mode in self.PLAY_MODES else 0)

        panel.SetSizer(grid.sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.ALL | wx.EXPAND, 15)
        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.FindWindowById(wx.ID_OK).SetLabel(_("Save"))
        self.SetSizer(outer)
        for control in (source, repeat, duration):
            control.Bind(wx.EVT_CHOICE, self.updateFields)
        self.Bind(wx.EVT_BUTTON, self.onSave, id=wx.ID_OK)
        self.updateFields()
        self.SetMinSize((520, self.GetSize().height))
        self.CentreOnParent()
        self.controls["timeHour"].SetFocus()

    def source_id(self):
        return self.sourceIds[self.controls["source"].GetSelection()]

    def updateFields(self, event=None):
        custom_duration = self.controls["durationMode"].GetSelection() == 1
        wanted = {
            "play_mode": alarm_mode_supported(self.context, self.source_id()),
            "url": self.source_id() == 2,
            "days": self.controls["recurrence"].GetSelection() == 4,
            "durationHour": custom_duration,
            "durationMinute": custom_duration,
        }
        for key, visible in wanted.items():
            self.grid.show(key, visible)
        self.panel.Layout()
        self.Fit()
        self.Layout()

    def onSave(self, event):
        c = self.controls
        recurrence = self.RECURRENCES[c["recurrence"].GetSelection()]
        if recurrence == "CUSTOM":
            recurrence = "ON_" + "".join(str(i) for i in range(7) if c["days"].IsChecked(i))
        supported = alarm_mode_supported(self.context, self.source_id())
        data = dict(
            time=f"{c['timeHour'].GetStringSelection()}:{c['timeMinute'].GetStringSelection()}",
            room=c["room"].GetSelection(), recurrence=recurrence,
            volume=c["volume"].GetValue(), enabled=c["enabled"].GetValue(),
            linked=c["linked"].GetValue(), source=self.source_id(), url=c["url"].GetValue(),
            duration=(f"{c['durationHour'].GetStringSelection()}:"
                      f"{c['durationMinute'].GetStringSelection()}"
                      if c["durationMode"].GetSelection() else ""),
            play_mode=self.PLAY_MODES[c["play_mode"].GetSelection()] if supported else "NORMAL")
        try:
            if recurrence == "ON_":
                raise ValueError(_("Select at least one day."))
            parse_alarm(data)
            if data["room"] < 0:
                raise ValueError(_("Select a room."))
            if data["source"] == 2 and not valid_url(data["url"]):
                raise ValueError(_("Enter an audio URL."))
        except ValueError as error:
            wx.MessageBox(str(error), _("Check alarm"), wx.OK | wx.ICON_ERROR, self)
            return
        self.result = data
        self.EndModal(wx.ID_OK)


class LocalFilesDialog(wx.Dialog):
    def __init__(self, parent, room):
        super().__init__(parent, title=_("Play local audio on {room}").format(room=room))
        self.result = None
        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.kind = wx.RadioBox(panel, label=_("Open"), choices=[_("File"), _("Folder")],
                                majorDimension=1, style=wx.RA_SPECIFY_ROWS)
        layout.Add(self.kind, 0, wx.EXPAND | wx.BOTTOM, 10)
        label = wx.StaticText(panel, label=_("Path"))
        layout.Add(label, 0, wx.BOTTOM, 4)
        self.path = wx.TextCtrl(panel)
        self.path.SetName(_("Path"))
        layout.Add(self.path, 0, wx.EXPAND | wx.BOTTOM, GAP)
        browse = wx.Button(panel, label=_("Browse..."))
        browse.Bind(wx.EVT_BUTTON, self.browse)
        layout.Add(browse, 0, wx.BOTTOM, GAP)
        self.recursive = wx.CheckBox(panel, label=_("Include subfolders"))
        self.recursive.Enable(False)
        layout.Add(self.recursive, 0, wx.BOTTOM, GAP)
        self.kind.Bind(wx.EVT_RADIOBOX, self.onKind)
        panel.SetSizer(layout)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.ALL | wx.EXPAND, 15)
        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.FindWindowById(wx.ID_OK).SetLabel(_("Play"))
        self.SetSizerAndFit(outer)
        self.SetMinSize((460, self.GetSize().height))
        self.CentreOnParent()
        self.Bind(wx.EVT_BUTTON, self.onPlay, id=wx.ID_OK)
        self.kind.SetFocus()

    def onKind(self, event):
        self.recursive.Enable(self.kind.GetSelection() == 1)

    def browse(self, event):
        from .localfiles import MIME
        if self.kind.GetSelection() == 1:
            dialog = wx.DirDialog(self, _("Choose audio folder"),
                                  style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
        else:
            patterns = ";".join("*" + extension for extension in MIME)
            dialog = wx.FileDialog(
                self, _("Choose audio file"),
                wildcard=_("Audio files") + f"|{patterns}|" + _("All files") + "|*.*",
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                self.path.SetValue(dialog.GetPath())
        finally:
            dialog.Destroy()

    def onPlay(self, event):
        path = self.path.GetValue().strip().strip('"')
        if not path:
            wx.MessageBox(_("Choose a file or folder."), _("Local audio"),
                          wx.OK | wx.ICON_ERROR, self)
            return
        self.result = dict(path=path,
                           recursive=self.kind.GetSelection() == 1 and self.recursive.GetValue())
        self.EndModal(wx.ID_OK)
