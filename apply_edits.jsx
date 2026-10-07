// Applies local/edits.json (written by apply.py) to PSD text layers.
// Each edit: {psd, layer (layer ID), old (expected full text), new (full text), tag}, plus for layers
// with more than one character style: keep_styles: true and style_map (see keepStylesEdit below).
// A layer is changed only if its current text still equals "old". PSDs that are already open
// with unsaved changes are skipped so nothing of the user's is overwritten.
#target photoshop

(function () {
    // edits.json is in the local/ folder next to this script; the log is written there too.
    // apply.py writes local/edits_target.txt with the path of another project's edits.json (MP_CONFIG).
    var editsPath = new File($.fileName).parent.fsName + "/local/edits.json";
    var target = new File(new File($.fileName).parent.fsName + "/local/edits_target.txt");
    if (target.exists) {
        target.encoding = "UTF-8";
        target.open("r");
        editsPath = target.readln();
        target.close();
    }
    var f = new File(editsPath);
    if (!f.exists) return;
    f.encoding = "UTF-8";
    f.open("r");
    var edits = eval("(" + f.read() + ")");
    f.close();

    var log = [];
    var s2t = stringIDToTypeID;
    function norm(s) { return s.replace(/\u0003/g, "\r").replace(/\r+$/, ""); }

    function selectLayerById(id) {
        var ref = new ActionReference();
        ref.putIdentifier(charIDToTypeID("Lyr "), id);
        var desc = new ActionDescriptor();
        desc.putReference(charIDToTypeID("null"), ref);
        desc.putBoolean(charIDToTypeID("MkVs"), false);
        executeAction(charIDToTypeID("slct"), desc, DialogModes.NO);
    }

    var installed = null;
    function isInstalled(name) {
        if (!installed) {
            installed = {};
            for (var n = 0; n < app.fonts.length; n++) installed[app.fonts[n].postScriptName] = true;
        }
        return installed[name] === true;
    }

    // A PostScript name that isn't installed renders in a fallback font (e.g. Myriad). Returns the
    // installed name to use instead ("X-Regular" <-> "X"), or "" if there is none.
    function installedTwin(name) {
        var tries = [name.replace(/-Regular$/, ""), name + "-Regular"];
        for (var t = 0; t < tries.length; t++) if (tries[t] !== name && isInstalled(tries[t])) return tries[t];
        return "";
    }

    // ---- Style-keeping edit (Action Manager) ----
    // Setting textItem.contents gives the whole layer one style. Instead, read the layer's textKey
    // descriptor, rebuild its style and paragraph ranges for the new text, and write it back, so
    // everything else (fonts per run, size, leading, tracking, colour, box/point text, warp,
    // transform, position) stays as it was.
    // e.style_map (from apply.py) says, for every character of the new text, which character of the
    // old text it takes its style from: a list of [count, old index, step] (step 1 = consecutive
    // characters, 0 = all from the same character). The old text's length stands for its end.

    function textKeyOf(layerId) {
        var ref = new ActionReference();
        ref.putIdentifier(charIDToTypeID("Lyr "), layerId);
        return executeActionGet(ref).getObjectValue(s2t("textKey"));
    }

    // Action Manager reports a range once per paragraph it touches, with the same from/to. Returns
    // the distinct ranges in order, or null unless they cover 0..length+1 without gaps or overlaps.
    function rangesOf(key, listKey, styleKey, length) {
        var list = key.getList(s2t(listKey)), seen = {}, out = [];
        for (var i = 0; i < list.count; i++) {
            var r = list.getObjectValue(i);
            var from = r.getInteger(s2t("from")), to = r.getInteger(s2t("to"));
            if (seen[from + ":" + to]) continue;
            seen[from + ":" + to] = true;
            out.push({from: from, to: to, style: r.getObjectValue(s2t(styleKey))});
        }
        out.sort(function (a, b) { return a.from - b.from; });
        for (var k = 0; k < out.length; k++) {
            if (out[k].from !== (k ? out[k - 1].to : 0) || out[k].to <= out[k].from) return null;
        }
        if (!out.length || out[out.length - 1].to < length + 1) return null;
        return out;
    }

    // index of the range each character (and the end, at "length") is in
    function rangeIndexPerChar(ranges, length) {
        var idx = [];
        for (var k = 0; k < ranges.length; k++)
            for (var c = ranges[k].from; c < ranges[k].to && c <= length; c++) idx[c] = k;
        return idx;
    }

    function expandMap(map, oldLength, newLength) {
        var src = [];
        for (var m = 0; m < map.length; m++)
            for (var n = 0; n < map[m][0]; n++) src.push(map[m][1] + n * map[m][2]);
        if (src.length !== newLength) return null;
        for (var s = 0; s < src.length; s++) if (src[s] < 0 || src[s] > oldLength) return null;
        src.push(oldLength);  // the end of the new text takes the old end's style
        return src;
    }

    function rangeList(listKey, styleKey, starts, ranges) {
        // starts: [{from, to, k}] -> ActionList of {from, to, styleKey: ranges[k].style}
        var list = new ActionList();
        for (var i = 0; i < starts.length; i++) {
            var d = new ActionDescriptor();
            d.putInteger(s2t("from"), starts[i].from);
            d.putInteger(s2t("to"), starts[i].to);
            d.putObject(s2t(styleKey), s2t(styleKey), ranges[starts[i].k].style);
            list.putObject(s2t(listKey), d);
        }
        return list;
    }

    function charStyleOf(style) {
        var size = 0;
        try { size = style.getUnitDoubleValue(s2t("size")); } catch (x) {}
        return style.getString(s2t("fontPostScriptName")) + "|" + Math.round(size * 1000);
    }

    // Returns {ok: true, note} or {ok: false, why}. Nothing is changed when ok is false.
    function keepStylesEdit(e) {
        var key = textKeyOf(e.layer);
        var text = key.getString(s2t("textKey")), newText = e["new"];
        if (key.hasKey(s2t("orientation")) &&
            typeIDToStringID(key.getEnumerationValue(s2t("orientation"))) !== "horizontal")
            return {ok: false, why: "vertical text"};
        if (text !== e.old) return {ok: false, why: "line-break characters differ from the plan"};
        var styles = rangesOf(key, "textStyleRange", "textStyle", text.length);
        var paras = rangesOf(key, "paragraphStyleRange", "paragraphStyle", text.length);
        if (!styles || !paras) return {ok: false, why: "style ranges don't cover the text"};
        var src = expandMap(e.style_map, text.length, newText.length);
        if (!src) return {ok: false, why: "style map doesn't fit the text"};
        var sIdx = rangeIndexPerChar(styles, text.length), pIdx = rangeIndexPerChar(paras, text.length);

        // character styles: consecutive new characters from the same old range form one range
        var sRuns = [], expected = [];
        for (var j = 0; j <= newText.length; j++) {
            var k = sIdx[src[j]];
            if (sRuns.length && sRuns[sRuns.length - 1].k === k) sRuns[sRuns.length - 1].to = j + 1;
            else sRuns.push({from: j, to: j + 1, k: k});
        }
        // paragraph styles: one range per paragraph (ended by \r or \u0003), styled like the old
        // paragraph its first character came from
        var pRuns = [], start = 0;
        for (var c = 0; c <= newText.length; c++) {
            if (c === newText.length || newText.charAt(c) === "\r" || newText.charAt(c) === "\u0003") {
                pRuns.push({from: start, to: c + 1, k: pIdx[src[start]]});
                start = c + 1;
            }
        }

        // Paragraph settings equal to the document's default style are left out of the descriptor,
        // and Photoshop would fill in its own defaults (e.g. the Every-line Composer) for them.
        // e.para_keep (from apply.py, read from the file) puts them back.
        for (var pk in e.para_keep || {}) {
            for (var pr = 0; pr < paras.length; pr++) {
                var pst = paras[pr].style;
                if (pst.hasKey(s2t(pk))) continue;
                if (typeof e.para_keep[pk] === "boolean") pst.putBoolean(s2t(pk), e.para_keep[pk]);
                else pst.putEnumerated(s2t(pk), s2t(pk), s2t(e.para_keep[pk]));
            }
        }

        // missing fonts: use the installed twin ("X-Regular" <-> "X") in every run that names one
        var notes = [], done = {};
        for (var r = 0; r < styles.length; r++) {
            var ps = styles[r].style.getString(s2t("fontPostScriptName"));
            if (isInstalled(ps)) continue;
            var twin = installedTwin(ps);
            if (twin) {
                styles[r].style.putString(s2t("fontPostScriptName"), twin);
                try {
                    var tf = app.fonts.getByName(twin);
                    styles[r].style.putString(s2t("fontName"), tf.family);
                    styles[r].style.putString(s2t("fontStyleName"), tf.style);
                } catch (fx) {}
            }
            if (!done[ps]) { notes.push("font " + ps + " -> " + (twin || "missing")); done[ps] = true; }
        }
        for (var q = 0; q < newText.length; q++) expected.push(charStyleOf(styles[sIdx[src[q]]].style));

        // manual kerning pairs: keep those whose characters are unchanged, at their new positions
        var oldToNew = {};
        for (var a = 0; a < newText.length; a++) {
            if (newText.charAt(a) === text.charAt(src[a]) && oldToNew[src[a]] === undefined) oldToNew[src[a]] = a;
        }
        if (key.hasKey(s2t("kerningRange"))) {
            var kl = key.getList(s2t("kerningRange")), kOut = new ActionList(), dropped = 0;
            for (var n = 0; n < kl.count; n++) {
                var kr = kl.getObjectValue(n), kf = kr.getInteger(s2t("from")), kt = kr.getInteger(s2t("to"));
                if (oldToNew[kf] === undefined || (kt > kf && oldToNew[kt - 1] !== oldToNew[kf] + (kt - 1 - kf))) {
                    dropped++;
                    continue;
                }
                kr.putInteger(s2t("from"), oldToNew[kf]);
                kr.putInteger(s2t("to"), oldToNew[kf] + (kt - kf));
                kOut.putObject(kl.getObjectType(n), kr);
            }
            key.putList(s2t("kerningRange"), kOut);
            if (dropped) notes.push(dropped + " manual kerning pair(s) dropped");
        }

        key.putString(s2t("textKey"), newText);
        key.putList(s2t("textStyleRange"), rangeList("textStyleRange", "textStyle", sRuns, styles));
        key.putList(s2t("paragraphStyleRange"), rangeList("paragraphStyleRange", "paragraphStyle", pRuns, paras));

        var tref = new ActionReference();
        tref.putEnumerated(charIDToTypeID("TxLr"), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
        var d = new ActionDescriptor();
        d.putReference(charIDToTypeID("null"), tref);
        d.putObject(charIDToTypeID("T   "), charIDToTypeID("TxLr"), key);
        var oldQuotes = app.preferences.smartQuotes;
        app.preferences.smartQuotes = false;  // otherwise straight quotes in the new text get curled
        try {
            executeAction(charIDToTypeID("setd"), d, DialogModes.NO);
        } finally {
            app.preferences.smartQuotes = oldQuotes;
        }

        // check what Photoshop made of it: text, and font + size of every character
        var after = textKeyOf(e.layer);
        if (after.getString(s2t("textKey")) !== newText) return {ok: false, why: "text came out different", written: true};
        var aStyles = rangesOf(after, "textStyleRange", "textStyle", newText.length);
        if (!aStyles) return {ok: false, why: "style ranges came out broken", written: true};
        var aIdx = rangeIndexPerChar(aStyles, newText.length), cache = {};
        for (var v = 0; v < newText.length; v++) {
            if (cache[aIdx[v]] === undefined) cache[aIdx[v]] = charStyleOf(aStyles[aIdx[v]].style);
            if (cache[aIdx[v]] !== expected[v]) {
                return {ok: false, written: true, why: "character " + v + " came out " + cache[aIdx[v]] + ", expected " + expected[v]};
            }
        }
        return {ok: true, note: notes.length ? " (" + notes.join(", ") + ")" : ""};
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
                    if (e.keep_styles) {
                        var state = doc.activeHistoryState, res;
                        try {
                            res = keepStylesEdit(e);
                        } catch (kerr) {
                            res = {ok: false, written: true, why: String(kerr)};
                        }
                        if (!res.ok) {
                            if (res.written) doc.activeHistoryState = state;  // undo it
                            log.push("MANUAL (" + res.why + "): " + e.tag);
                            continue;
                        }
                        changed++;
                        log.push("OK: " + e.tag + res.note);
                        continue;
                    }
                    var fontBefore = "";
                    try { fontBefore = ti.font; } catch (fe) {}
                    ti.contents = e["new"].replace(/\u0003/g, "\r");
                    // Photoshop falls back to another font (e.g. Myriad) when the saved PostScript
                    // name isn't installed; put the original back, or its "-Regular"-less twin.
                    // The saved name can also be one that isn't installed at all (e.g. "CCMeanwhile-Regular"
                    // when only "CCMeanwhile" is), which renders as a fallback while still reporting the old name.
                    var fontNote = "";
                    if (fontBefore && (ti.font !== fontBefore || !isInstalled(fontBefore))) {
                        var tries = [fontBefore, fontBefore.replace(/-Regular$/, "")];
                        for (var t = 0; t < tries.length; t++) {
                            if (!isInstalled(tries[t])) continue;
                            try { ti.font = tries[t]; } catch (fe2) {}
                            break;
                        }
                        if (ti.font !== fontBefore) fontNote = " (font " + fontBefore + " -> " + ti.font + ")";
                    }
                    changed++;
                    log.push("OK: " + e.tag + fontNote);
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
