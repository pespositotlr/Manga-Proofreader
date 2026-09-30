// Applies local/edits.json (written by apply.py) to PSD text layers.
// Each edit: {psd, layer (layer ID), old (expected full text), new (full text), tag}.
// A layer is changed only if its current text still equals "old". PSDs that are already open
// with unsaved changes are skipped so nothing of the user's is overwritten.
#target photoshop

(function () {
    // edits.json is in the local/ folder next to this script; the log is written there too
    var editsPath = new File($.fileName).parent.fsName + "/local/edits.json";
    var f = new File(editsPath);
    if (!f.exists) return;
    f.encoding = "UTF-8";
    f.open("r");
    var edits = eval("(" + f.read() + ")");
    f.close();

    var log = [];
    function norm(s) { return s.replace(/\u0003/g, "\r").replace(/\r+$/, ""); }

    function selectLayerById(id) {
        var ref = new ActionReference();
        ref.putIdentifier(charIDToTypeID("Lyr "), id);
        var desc = new ActionDescriptor();
        desc.putReference(charIDToTypeID("null"), ref);
        desc.putBoolean(charIDToTypeID("MkVs"), false);
        executeAction(charIDToTypeID("slct"), desc, DialogModes.NO);
    }

    var byPsd = {}, order = [];
    for (var i = 0; i < edits.length; i++) {
        var p = edits[i].psd;
        if (!byPsd[p]) { byPsd[p] = []; order.push(p); }
        byPsd[p].push(edits[i]);
    }

    var oldDialogs = app.displayDialogs;
    app.displayDialogs = DialogModes.NO;
    for (var k = 0; k < order.length; k++) {
        var path = order[k], file = new File(path), doc = null, wasOpen = false;
        for (var d = 0; d < app.documents.length; d++) {
            try {
                if (app.documents[d].fullName.fsName.toLowerCase() === file.fsName.toLowerCase()) {
                    doc = app.documents[d]; wasOpen = true;
                }
            } catch (e) {}
        }
        if (wasOpen && !doc.saved) {
            log.push("SKIPPED (open with unsaved changes): " + path);
            continue;
        }
        try {
            if (wasOpen) app.activeDocument = doc; else doc = app.open(file);
            var changed = 0, list = byPsd[path];
            for (var j = 0; j < list.length; j++) {
                var e = list[j];
                try {
                    selectLayerById(e.layer);
                    var ti = doc.activeLayer.textItem;
                    if (norm(ti.contents) !== norm(e.old)) {
                        log.push("CHANGED SINCE PLAN, skipped: " + e.tag);
                        continue;
                    }
                    ti.contents = e["new"].replace(/\u0003/g, "\r");
                    changed++;
                    log.push("OK: " + e.tag);
                } catch (err) {
                    log.push("ERROR " + e.tag + ": " + err);
                }
            }
            if (changed) doc.save();
            if (!wasOpen) doc.close(SaveOptions.DONOTSAVECHANGES);
        } catch (err2) {
            log.push("ERROR opening " + path + ": " + err2);
        }
    }
    app.displayDialogs = oldDialogs;

    var out = new File(new File(editsPath).parent.fsName + "/apply_log.txt");
    out.encoding = "UTF-8";
    out.open("w");
    out.write(log.join("\n"));
    out.close();
})();
