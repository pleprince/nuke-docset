import argparse
import re
import sys
from functools import partial
import logging
import shutil
import sqlite3
from pathlib import Path
from bs4 import BeautifulSoup
from bs4.formatter import HTMLFormatter


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>%(name)s</string>
    <key>CFBundleName</key>
    <string>%(name)s</string>
    <key>DocSetPlatformFamily</key>
    <string>%(name)s</string>
    <key>isDashDocset</key>
    <true/>
    <key>dashIndexFilePath</key>
    <string>index.html</string>
</dict>
</plist>
"""


def get_logger():
    logger = logging.getLogger("nuke-docset")
    if not logger.handlers:
        hdlr = logging.StreamHandler()
        fmt = logging.Formatter("[%(name)s] %(levelname)8s:  %(funcName)s: %(message)s")
        hdlr.setFormatter(fmt)
        logger.addHandler(hdlr)
        logger.propagate = False
        # add verbose: the debug messages are only visible if Nuke was launched
        # with the -V 2 flag.
        logging.addLevelName(15, "VERBOSE")
        setattr(logging, "VERBOSE", 15)
        setattr(logger, "verbose", partial(logger.log, logging.VERBOSE))
        logger.setLevel(logging.INFO)
        logger.verbose(
            "Log level: %r", logging.getLevelName(logger.getEffectiveLevel())
        )
    return logger


class ProgressBar:
    def __init__(self, vmax, width=100):
        self.vmax = int(vmax)
        self.width = int(width)
        self.inc = 0
        sys.stdout.write("\33[?25l")    # hide shell cursor

    def increment(self):
        self.inc = min(self.vmax, self.inc + 1)
        ratio = float(self.inc) / float(self.vmax)
        done = int(ratio * self.width)
        remaining = self.width - done
        percent = int(ratio * 100.0)
        sys.stdout.write("\r[%s%s] %d%%" % ("=" * done, " " * remaining, percent))
        sys.stdout.flush()
        # new line when done
        if self.inc == self.vmax:
            sys.stdout.write("\33[?25h")    # show shell cursor
            sys.stdout.write("\n")


def mk_structure(name):
    cwd_path = Path.cwd()
    root_path = Path(cwd_path, "%s.docset" % name)
    if root_path.exists():
        shutil.rmtree(root_path)
    root_path.mkdir()

    res_path = Path(root_path, "Contents", "Resources")
    res_path.mkdir(parents=True)

    doc_path = Path(root_path, "Contents", "Resources", "Documents")
    shutil.copytree(args.directory, doc_path)

    plistp = Path(root_path, "Contents", "Info.plist")
    plistp.write_text(PLIST % {"name": name}, encoding="utf-8")

    db_path = Path(res_path, "docSet.dsidx")

    shutil.copy2(Path(cwd_path, "icon.png"), Path(root_path, "icon.png"))

    return db_path, doc_path


def init_db(db_path):
    log = get_logger()
    log.debug("Connecting to db...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    log.debug("Cleaning db...")
    try:
        cur.execute("DROP TABLE searchIndex;")
    except sqlite3.OperationalError as err:
        log.debug(err)
        # raise RuntimeError('Failed to clear db ! Aborting !')
    finally:
        cur.execute(
            "CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT);"
        )
        cur.execute("CREATE UNIQUE INDEX anchor ON searchIndex (name, type, path);")
        log.info("Created tables")
    return conn


def found(*args):
    if not getattr(found, "set", None):
        setattr(found, "set", set())
    if args:
        found.set.add(args[0])
    else:
        return found.set


def get_parent_by_type(elmt, htype):
    it = elmt
    while it.name != htype:
        it = it.parent
    return it


def memItemRightList(h2, category, html):
    table = get_parent_by_type(h2, "table")
    items = [it for it in table.find_all("td")]
    result = {}
    for i, item in enumerate(items):
        if item.a:
            for ia in item.find_all("a"):
                if "el" not in ia.get("class", []):
                    continue
                if "inherit" in item.parent.get("class", []):
                    continue
                name = ia.string
                url = ia.get("href")
                result[name] = url
        elif item.b and html:
            left = item.parent.find("td", {"class": "memItemLeft"})
            if not (left and left.a):
                continue
            if "anchor" in left.a.get("class"):
                name = item.b.string
                url = "%s#%s" % (html, left.a.get("id"))
                result[name] = url
    return result


def write_entries_by_cat(data, name, category, html=None):
    cur, h2, class_name = data
    if h2.a.get("name") == name:
        for t_name, t_url in memItemRightList(h2, category, html).items():
            if t_name and t_url:
                cur.execute(
                    "INSERT OR IGNORE INTO searchIndex(name, type, path) "
                    "VALUES ('{class_name}::{type_name}', '{category}', '{path}')".format(
                        class_name=class_name,
                        type_name=t_name,
                        path=t_url,
                        category=category,
                    )
                )
        return True
    return False


def write_class_entries(conn, html):
    log = get_logger()
    log.debug("  > %s", html)
    class_name = re.search("\\w*_\d*(\\w+)\\.html", html.name).group(1)
    cur = conn.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO searchIndex(name, type, path) "
        "VALUES ('{name}', 'Class', '{path}')".format(name=class_name, path=html.name)
    )

    html_data = html.read_text()
    soup = BeautifulSoup(html_data, "html.parser")
    hn = html.name

    for h2 in soup.find_all("h2", {"class": "groupheader"}):
        # if h2.a:
        #     found(h2.a.get("name"))

        data = (cur, h2, class_name)

        if not h2.a:
            continue
        elif write_entries_by_cat(data, "typedef-members", "Type", html=hn):
            continue
        elif write_entries_by_cat(data, "pub-types", "Type", html=hn):
            continue
        elif write_entries_by_cat(data, "pro-types", "Type", html=hn):
            continue
        elif write_entries_by_cat(data, "pub-methods", "Method", html=hn):
            continue
        elif write_entries_by_cat(data, "pub-static-methods", "Function", html=hn):
            continue
        elif write_entries_by_cat(data, "pro-methods", "Method", html=hn):
            continue
        elif write_entries_by_cat(data, "pro-static-methods", "Function", html=hn):
            continue
        elif write_entries_by_cat(data, "pub-attribs", "Attribute", html=hn):
            continue
        elif write_entries_by_cat(data, "pro-attribs", "Attribute", html=hn):
            continue
        elif write_entries_by_cat(data, "pub-static-attribs", "Attribute", html=hn):
            continue
        elif write_entries_by_cat(data, "pro-static-attribs", "Attribute", html=hn):
            continue


def write_header_entries(conn, html):
    log = get_logger()
    log.debug("  > %s", html)
    namespace = re.search("(\\w*)_8h\\.html", html.name).group(1)
    cur = conn.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO searchIndex(name, type, path) "
        "VALUES ('{name}', 'Namespace', '{path}')".format(
            name=namespace, path=html.name
        )
    )

    html_data = html.read_text()
    soup = BeautifulSoup(html_data, "html.parser")
    hn = html.name

    for h2 in soup.find_all("h2", {"class": "groupheader"}):
        # if h2.a:
        #     found(h2.a.get("name"))

        data = (cur, h2, namespace)

        if not h2.a:
            continue
        elif write_entries_by_cat(data, "define-members", "Macro", html=hn):
            continue
        elif write_entries_by_cat(data, "enum-members", "Enum", html=hn):
            continue
        elif write_entries_by_cat(data, "func-members", "Function", html=hn):
            continue
        elif write_entries_by_cat(data, "typedef-members", "Type", html=hn):
            continue
        elif write_entries_by_cat(data, "var-members", "Variable", html=hn):
            continue


def write_db_entries(conn, html):
    if html.name.startswith("class"):
        write_class_entries(conn, html)
    else:
        write_header_entries(conn, html)


def mk_database(db_path, doc_path):
    log = get_logger()
    html_files = [f for f in doc_path.glob("*.html")]
    pbar = ProgressBar(len(html_files), width=55)
    conn = init_db(db_path)
    rex = re.compile("((classDD_\\w*)|(\\w*_8h))\\.html")
    for html in html_files:
        if not re.match(rex, html.name) or "-members" in html.name:
            pbar.increment()
            continue
        write_db_entries(conn, html)
        pbar.increment()

    # print found header categories
    for f in sorted(found()):
        log.info("Found: %r", f)

    conn.commit()
    log.info("commit done")
    conn.close()
    log.info("connection closed")


def mk_docset(args):
    log = get_logger()
    log.info("Generating docset...")
    db_path, doc_path = mk_structure(args.name)
    mk_database(db_path, doc_path)


if __name__ == "__main__":
    log = get_logger()
    log.info("Starting up...")

    parser = argparse.ArgumentParser(
        description="Builds a docset from a directory full of html files."
    )
    parser.add_argument("directory")
    parser.add_argument("-n", "--name", action="store", required=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    try:
        args = parser.parse_args()
    except BaseException as err:
        log.error(err)
        parser.print_help()
    else:
        if args.verbose:
            log.setLevel(logging.DEBUG)
        mk_docset(args)
    log.info("done !")
