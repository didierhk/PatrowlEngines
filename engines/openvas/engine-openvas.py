#!/usr/bin/python
# -*- coding: utf-8 -*-
"""OpenVAS PatrOwl engine application."""

import os
import sys
import json
from re import search as re_search
from subprocess import check_output
import threading
import time
import urlparse
import xml.etree.ElementTree as ET

# Third party library imports
from flask import Flask, request, jsonify
from dns.resolver import query

# Own library
from PatrowlEnginesUtils.PatrowlEngine import _json_serial
from PatrowlEnginesUtils.PatrowlEngine import PatrowlEngine
from PatrowlEnginesUtils.PatrowlEngine import PatrowlEngineFinding
from PatrowlEnginesUtils.PatrowlEngineExceptions import PatrowlEngineExceptions

# Debug
# from pdb import set_trace as st

app = Flask(__name__)
APP_DEBUG = False
APP_HOST = "0.0.0.0"
APP_PORT = 5016
APP_MAXSCANS = 5
APP_ENGINE_NAME = "openvas"
APP_BASE_DIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_OV_PROFILE = "Full and fast"
DEFAULT_OV_PORTLIST = "patrowl-all_tcp"

engine = PatrowlEngine(
    app=app,
    base_dir=APP_BASE_DIR,
    name=APP_ENGINE_NAME,
    max_scans=APP_MAXSCANS
)

this = sys.modules[__name__]
this.openvas_cli = None
this.openvas_portlists = {}


def _json_serial(obj):
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        # if the obj is uuid, we simply return the value of uuid
        return obj.hex
    raise TypeError("Type %s not serializable" % type(obj))

def omp_cmd(command):
    """ This function returns the output of an 'omp' command """
    omp_cmd_base = ["omp", "-h", engine.scanner["options"]["omp_host"]["value"], "-p", engine.scanner["options"]["omp_port"]["value"], "-u", engine.scanner["options"]["omp_username"]["value"], "-w", engine.scanner["options"]["omp_password"]["value"]]
    try:
        result = check_output(omp_cmd_base + command)
    except Exception as e:
        result = ''
    return result

def get_target(target_name):
    """
    This function returns the target_id of a target. If not, it returns None
    """
    result = omp_cmd(["--get-targets"]).split('\n')
    for target in result:
        if target_name in target:
            return target.split(' ')[0]
    return None

def create_target(target_name):
    """
    This function creates a target in OpenVAS and returns its target_id
    """
    result = omp_cmd(['--xml', '<create_target><name>%s</name><hosts>%s</hosts><ssh_credential id="%s"><port>%s</port></ssh_credential></create_target>' % (target_name, target_name, '1b6f3984-0378-4088-9575-805730d77282', 22)])
    if not 'status_text="OK, resource created" status="201"' in result:
        return None
    return result.split('"')[1]

def get_task(target_name):
    """
    This function returns the task_id
    """
    result = omp_cmd(["--get-tasks"]).split('\n')
    for target in result:
        if target_name in target:
            return target.split(' ')[0]
    return None

def create_task(target_name, target_id):
    """
    This function creates a task_id in OpenVAS and returns its task_id
    """
    result = omp_cmd(["-C", "-c", "698f691e-7489-11df-9d8c-002264764cea", "--name", target_name, "--target", target_id]).split('\n')[0]
    if re_search('^[a-z0-9-]+$', result) is None:
        return None
    return result

def start_task(task_id):
    """
    This function starts a task and returns a report_id
    """
    result = omp_cmd(["-S", task_id]).split('\n')[0]
    if re_search('^[a-z0-9-]+$', result) is None:
        return None
    return result

def get_last_report(task_id):
    """
    This function returns the last report_id of a task_id
    """
    result = omp_cmd(["--get-tasks", task_id]).split('\n')
    return result[-2].split('  ')[1]

def get_report_status(task_id, report_id):
    """
    This function get the status of a report_id
    """
    result = omp_cmd(["--get-tasks", task_id]).split('\n')
    for report in result:
        if report_id in report:
            return report.split('  ')[2]
    return None

def is_ip(string):
    """ This dummy function returns True is the string is probably an IP """
    return re_search('[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+', string) is not None

@app.errorhandler(404)
def page_not_found(e):
    """Page not found."""
    return engine.page_not_found()


@app.errorhandler(PatrowlEngineExceptions)
def handle_invalid_usage(error):
    """Invalid request usage."""
    response = jsonify(error.to_dict())
    response.status_code = 404
    return response


@app.route('/')
def default():
    """Route by default."""
    return engine.default()


@app.route('/engines/openvas/')
def index():
    """Return index page."""
    return engine.index()


@app.route('/engines/openvas/liveness')
def liveness():
    """Return liveness page."""
    return engine.liveness()


@app.route('/engines/openvas/readiness')
def readiness():
    """Return readiness page."""
    return engine.readiness()


@app.route('/engines/openvas/test')
def test():
    """Return test page."""
    return engine.test()


@app.route('/engines/openvas/info')
def info():
    """Get info on running engine."""
    return engine.info()


@app.route('/engines/openvas/clean')
def clean():
    """Clean all scans."""
    return engine.clean()


@app.route('/engines/openvas/clean/<scan_id>')
def clean_scan(scan_id):
    """Clean scan identified by id."""
    return engine.clean_scan(scan_id)


@app.route('/engines/openvas/status')
def status():
    """Get status on engine and all scans."""
    return engine.getstatus()


@app.route('/engines/openvas/status/<scan_id>')
def status_scan(scan_id):
    """Get status on scan identified by id."""
    res = {"page": "status", "status": "UNKNOWN"}
    if scan_id not in engine.scans.keys():
        res.update({"status": "error", "reason": "scan_id '{}' not found".format(scan_id)})

    if engine.scans[scan_id]["status"] == "ERROR":
        res.update({"status": "error", "reason": "todo"})
        return jsonify(res)

    report_status = 'Done'
    assets = engine.scans[scan_id]['assets']
    for asset in assets:
        asset_status = get_report_status(assets[asset]['task_id'], assets[asset]['report_id'])
        assets[asset]['status'] = asset_status
        if asset_status != 'Done':
            report_status = asset_status

    engine.scans[scan_id]["scan_status"] = report_status

    if engine.scans[scan_id]["scan_status"] == "Done":
        res.update({"status": "FINISHED"})
        engine.scans[scan_id]["status"] = "FINISHED"
        # Get the last version of the report
        try:
            _scan_urls(scan_id)
        except Exception as e:
            res.update({"status": "error", "reason": "scan_urls did not worked ! (%s)" % e})
            return jsonify(res)
    else:
        res.update({"status": "SCANNING"})
        for asset in assets:
            res.update({asset: assets[asset]['status']})
        engine.scans[scan_id]["status"] = "SCANNING"

    return jsonify(res)


@app.route('/engines/openvas/stopscans')
def stop():
    """Stop all scans."""
    return engine.stop()


@app.route('/engines/openvas/stop/<scan_id>')
def stop_scan(scan_id):
    res = {"page": "status", "status": "success"}
    """Stop scan identified by id."""
    if scan_id not in engine.scans.keys():
        res.update({"status": "error", "reason": "scan_id '{}' not found".format(scan_id)})

    this.openvas_cli.stop_audit(scan_id)
    if engine.scans[scan_id]['status'] not in ["FINISHED", "ERROR"]:
        engine.scans[scan_id]['status'] = "STOPPED"

    return res


@app.route('/engines/openvas/getreport/<scan_id>')
def getreport(scan_id):
    """Get report on finished scans."""
    return engine.getreport(scan_id)


def _loadconfig():
    conf_file = APP_BASE_DIR+'/openvas.json'
    if os.path.exists(conf_file):
        json_data = open(conf_file)
        engine.scanner = json.load(json_data)
        engine.scanner['status'] = "INIT"

        # Check omp connectivity
        if set(["omp_host", "omp_port", "omp_username", "omp_password"]).issubset(engine.scanner['options'].keys()):
            try:
                this.openvas_cli = VulnscanManager(
                    str(engine.scanner['options']['omp_host']['value']),
                    str(engine.scanner['options']['omp_username']['value']),
                    str(engine.scanner['options']['omp_password']['value']),
                    int(engine.scanner['options']['omp_port']['value']))
            except VulnscanException as e:
                print("Error: {}".format(e))
        else:
            print("Error: missing required options in config file".format(conf_file))
            engine.scanner['status'] = "ERROR"
            return {"status": "error", "reason": "missing required options"}

        for pl_name, pl_data in this.openvas_cli.get_port_lists().items():
            this.openvas_portlists.update({pl_name: pl_data['id']})

        # Create custom port lists
        if "patrowl-all_tcp" not in this.openvas_portlists.keys():
            new_pl_id = this.openvas_cli.create_port_list(
                name="patrowl-all_tcp",
                port_range="T:1-65535"
            )
            this.openvas_portlists.update({"patrowl-all_tcp": new_pl_id})

        if "patrowl-quick_tcp" not in this.openvas_portlists.keys():
            new_pl_id = this.openvas_cli.create_port_list(
                name="patrowl-quick_tcp",
                port_range="T:21-80,T:443,U:53"
            )
            this.openvas_portlists.update({"patrowl-quick_tcp": new_pl_id})

        if "patrowl-tcp_80" not in this.openvas_portlists.keys():
            new_pl_id = this.openvas_cli.create_port_list(
                name="patrowl-tcp_80",
                port_range="T:80"
            )
            this.openvas_portlists.update({"patrowl-tcp_80": new_pl_id})

        if "patrowl-tcp_443" not in this.openvas_portlists.keys():
            new_pl_id = this.openvas_cli.create_port_list(
                name="patrowl-tcp_443",
                port_range="T:443"
            )
            this.openvas_portlists.update({"patrowl-tcp_443": new_pl_id})

        if "patrowl-tcp_22" not in this.openvas_portlists.keys():
            new_pl_id = this.openvas_cli.create_port_list(
                name="patrowl-tcp_22",
                port_range="T:22"
            )
            this.openvas_portlists.update({"patrowl-tcp_22": new_pl_id})

        engine.scanner['status'] = "READY"
    else:
        print("Error: config file '{}' not found".format(conf_file))
        engine.scanner['status'] = "ERROR"
        return {"status": "error", "reason": "config file not found"}


@app.route('/engines/openvas/reloadconfig', methods=['GET'])
def reloadconfig():
    res = {"page": "reloadconfig"}
    _loadconfig()
    res.update({"config": engine.scanner})
    return jsonify(res)


@app.route('/engines/openvas/startscan', methods=['POST'])
def start_scan():
    res = {"page": "startscan"}

    # Check the scanner is ready to start a new scan
    if len(engine.scans) == APP_MAXSCANS:
        res.update({
            "status": "error",
            "reason": "Scan refused: max concurrent active scans reached ({})".format(APP_MAXSCANS)
        })
        return jsonify(res)

    status()
    if engine.scanner['status'] != "READY":
        res.update({
            "status": "refused",
            "details": {
                "reason": "scanner not ready",
                "status": engine.scanner['status']
            }})
        return jsonify(res)

    data = json.loads(request.data)
    if 'assets' not in data.keys() or 'scan_id' not in data.keys():
        res.update({
            "status": "refused",
            "details": {
                "reason": "arg error, something is missing ('assets' ?)"
            }})
        return jsonify(res)

    assets = []
    for asset in data["assets"]:
        # Value
        if "value" not in asset.keys() or not asset["value"]:
            res.update({
                "status": "error",
                "reason": "arg error, something is missing ('asset.value')"
            })
            return jsonify(res)

        # Supported datatypes
        if asset["datatype"] not in engine.scanner["allowed_asset_types"]:
            res.update({
                "status": "error",
                "reason": "arg error, bad value for '{}' datatype (not supported)".format(asset["value"])
            })
            return jsonify(res)

        if asset["datatype"] == "url":
            parsed_uri = urlparse.urlparse(asset["value"])
            asset["value"] = parsed_uri.netloc

        assets.append(asset["value"])

    scan_id = str(data['scan_id'])

    if data['scan_id'] in engine.scans.keys():
        res.update({
            "status": "refused",
            "details": {
                "reason": "scan '{}' already launched".format(data['scan_id']),
            }
        })
        return jsonify(res)

    scan = {
        'assets':       assets,
        'threads':      [],
        'options':      data['options'],
        'scan_id':      scan_id,
        'ov_scan_id':   "",
        'ov_target_id': "",
        'ov_profile':   ov_profile,
        'ov_port_list': ov_port_list,
        'status':       "STARTED",
        'lock':         False,
        'started_at':   int(time.time() * 1000),
        'issues':       [],
        'summary':      {}
    }

    assets_failure = list()
    scan["assets"] = dict()

    for asset in assets:
        print('Start %s' % asset)
        target_id = get_target(asset)
        if target_id is None:
            print('Create target %s' % asset)
            target_id = create_target(asset)
        if target_id is None:
            print('Fail to create target %s' % asset)
            assets_failure.append(asset)
        else:
            task_id = get_task(asset)
            if task_id is None:
                print('Create task %s' % asset)
                task_id = create_task(asset, target_id)
            if task_id is None:
                print('Fail to create task %s' % asset)
                assets_failure.append(asset)
            else:
                report_id = start_task(task_id) # None to get last report
                if report_id is None:
                    print('Get last report of %s' % task_id)
                    report_id = get_last_report(task_id)
                if report_id is None:
                    print('Fail to start task %s' % task_id)
                    assets_failure.append(asset)
                else:
                    print('OK for report_id %s' % report_id)
                    scan['assets'].update({asset: {'task_id': task_id, 'report_id': report_id, 'status': 'accepted'}})


    if scan["assets"] == dict():
        res.update({
            "status": "refused",
            "details": {
                "reason": "scan '{}' already launched".format(data['scan_id']),
            }
        })
        return jsonify(res)

    engine.scans.update({scan_id: scan})
    thread = threading.Thread(target=_scan, args=(scan_id,))
    thread.start()
    engine.scans[scan_id]['threads'].append(thread)

    res.update({
        "status": "accepted",
        "details": {
            "scan_id": scan['scan_id']
        }
    })

    return jsonify(res)


def _scan_urls(scan_id):
    # Is it locked ?
    if engine.scans[scan_id]["lock"]:
        print("locked")
        return True

    # Does the scan is terminated ?
    if "scan_status" in engine.scans[scan_id].keys():
        scan_status = engine.scans[scan_id]["scan_status"]
    else:
        return True
    if scan_status != "Done":
        return True

    engine.scans[scan_id]["lock"] = True
    print("lock on")

    assets = []
    for asset in engine.scans[scan_id]['assets']:
        assets.append(asset)

    ov_profile = engine.scans[scan_id]["ov_profile"]
    ov_port_list = engine.scans[scan_id]["ov_port_list"]

    # Start scan
    Sem = Semaphore(0)
    ov_scan_id, ov_target_id = this.openvas_cli.launch_scan(
        target=assets,
        profile=ov_profile,
        port_list=ov_port_list,
        callback_end=partial(lambda x: x.release(), Sem)
    )
    engine.scans[scan_id].update({
        'ov_scan_id':   ov_scan_id,
        'ov_target_id': ov_target_id,
        'scan_status':  "SCANNING",
        'status':  "SCANNING"
    })
    Sem.acquire()
    # Finished scan

    print("lock off")
    engine.scans[scan_id]["lock"] = False
    return True

    issues, summary = _parse_results(scan_id)

def get_report(asset, scan_id):
    """Get report."""
    report_id = engine.scans[scan_id]["assets"][asset]['report_id']
    issues = []
    issue_id = 1

    if not os.path.isfile('results/openvas_report_%s_%s.xml' % (scan_id, asset)):
        result = omp_cmd(["--get-report", report_id])
        result_file = open("results/openvas_report_%s_%s.xml" % (scan_id, asset), "w")
        result_file.write(result)
        result_file.close()

    try:
        tree = ET.parse("results/openvas_report_%s_%s.xml" % (scan_id, asset))
    except Exception:
        # No Element found in XML file
        return False

    report = tree.getroot().find("report").find("report")

    if is_ip(asset):
        resolved_asset_ip = asset
    else:
        # Let's suppose it's a fqdn then...
        try:
            resolved_asset_ip = query(asset).response.answer[0].to_text().split(" ")[-1]
        except Exception as e:
            # What is that thing ?
            return issues

    for result in report.find("results").findall("result"):
        issue_meta = {}
        issue_name = result.find("name").text
        issue_desc = result.find("description").text
        host_ip = result.find("host").text
        assets = all_assets[host_ip]
        host_port = result.find("port").text

def _parse_results(scan_id):
    while engine.scans[scan_id]["lock"]:
        print("report is not terminated yet, going to sleep")
        time.sleep(10)

    issues = []
    summary = {}

    nb_vulns = {
        "info": 0,
        "low": 0,
        "medium": 0,
        "high": 0
    }
    timestamp = int(time.time() * 1000)

    for asset in engine.scans[scan_id]["findings"]:
        if engine.scans[scan_id]["findings"][asset]["issues"]:
            report_id = engine.scans[scan_id]['assets'][asset]['report_id']
            description = ''
            cvss_max = float(0)
            for eng in engine.scans[scan_id]["findings"][asset]["issues"]:
                if float(eng[0]) > 0:
                    cvss_max = max(float(eng[0]), cvss_max)
                    description = description + "[%s] CVSS: %s - Associated CVE : %s" % (eng[2], eng[0], eng[1]) + "\n"
            description = description + "For more detail go to 'https://%s/omp?cmd=get_report&report_id=%s'" % (engine.scanner["options"]["omp_host"]["value"], report_id)

            criticity = "high"
            if cvss_max == 0:
                criticity = "info"
            elif cvss_max < 4.0:
                criticity = "low"
            elif cvss_max < 7.0:
                criticity = "medium"

            nb_vulns[criticity] += 1

            issues.append({
                "issue_id": len(issues)+1,
                "severity": criticity, "confidence": "certain",
                "target": {"addr": [asset], "protocol": "http"},
                "title": "'{}' identified in openvas".format(asset),
                "solution": "n/a",
                "metadata": {},
                "type": "openvas_report",
                "timestamp": timestamp,
                "description": description,
            })

    summary = {
        "nb_issues": len(issues),
        "nb_info": nb_vulns["info"],
        "nb_low": nb_vulns["low"],
        "nb_medium": nb_vulns["medium"],
        "nb_high": nb_vulns["high"],
        "nb_critical": 0,
        "engine_name": "openvas",
        "engine_version": engine.scanner["version"]
    }

    return issues, summary


@app.route('/engines/openvas/getfindings/<scan_id>', methods=['GET'])
def getfindings(scan_id):
    res = {"page": "getfindings", "scan_id": scan_id}

    # Check if the scan_id exists
    if scan_id not in engine.scans.keys():
        res.update({"status": "error", "reason": "scan_id '{}' not found".format(scan_id)})
        return jsonify(res)

    # Check if the scan is finished
    status()
    if engine.scans[scan_id]['status'] != "FINISHED":
        res.update({"status": "error", "reason": "scan_id '{}' not finished (status={})".format(scan_id, engine.scans[scan_id]['status'])})
        return jsonify(res)

    scan = {
        "scan_id": scan_id,
        "assets": engine.scans[scan_id]['assets'],
        "options": engine.scans[scan_id]['options'],
        "status": engine.scans[scan_id]['status'],
        "started_at": engine.scans[scan_id]['started_at'],
        "finished_at": engine.scans[scan_id]['finished_at']
    }

    summary = engine.scans[scan_id]['summary']
    issues = engine.scans[scan_id]['issues']

    # Store the findings in a file
    with open(APP_BASE_DIR+"/results/openvas_"+scan_id+".json", 'w') as report_file:
        json.dump({
            "scan": scan,
            "summary": summary,
            "issues": issues
        }, report_file, default=_json_serial)

    # Remove the scan from the active scan list
    clean_scan(scan_id)

    res.update({"scan": scan, "summary": summary, "issues": issues, "status": "success"})
    return jsonify(res)


@app.before_first_request
def main():
    """First function called."""
    if not os.path.exists(APP_BASE_DIR+"/results"):
        os.makedirs(APP_BASE_DIR+"/results")
    _loadconfig()


if __name__ == '__main__':
    engine.run_app(app_debug=APP_DEBUG, app_host=APP_HOST, app_port=APP_PORT)
