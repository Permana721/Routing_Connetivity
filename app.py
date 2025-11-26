# app.py
import re
import io
import zipfile
import xml.etree.ElementTree as ET
from flask import Flask, request, jsonify, render_template, send_file
import requests

app = Flask(__name__, template_folder="templates", static_folder="static")

# ---------- helpers ----------
def extract_coords_from_kml_text(kml_text):
    try:
        root = ET.fromstring(kml_text)
    except ET.ParseError:
        kml_text = kml_text.strip()
        try:
            root = ET.fromstring(kml_text)
        except Exception:
            return []
    coords = []
    # try namespaced
    for coord in root.findall('.//{http://www.opengis.net/kml/2.2}coordinates'):
        text = coord.text
        if not text:
            continue
        parts = text.strip().split()
        for p in parts:
            comps = p.split(',')
            if len(comps) >= 2:
                try:
                    lon = float(comps[0]); lat = float(comps[1])
                    coords.append((lon, lat))
                except:
                    continue
    if not coords:
        for coord in root.findall('.//coordinates'):
            text = coord.text
            if not text: continue
            parts = text.strip().split()
            for p in parts:
                comps = p.split(',')
                if len(comps) >= 2:
                    try:
                        lon = float(comps[0]); lat = float(comps[1])
                        coords.append((lon, lat))
                    except:
                        continue
    return coords

def extract_two_points_from_kmz_file(file_stream):
    try:
        z = zipfile.ZipFile(file_stream)
    except zipfile.BadZipFile:
        return None, "Not a valid KMZ/ZIP"
    kml_name = None
    for name in z.namelist():
        if name.lower().endswith('.kml'):
            kml_name = name; break
    if not kml_name:
        return None, "KMZ has no KML file"
    kml_bytes = z.read(kml_name)
    try:
        kml_text = kml_bytes.decode('utf-8')
    except:
        try:
            kml_text = kml_bytes.decode('latin-1')
        except:
            kml_text = kml_bytes.decode(errors='ignore')
    coords = extract_coords_from_kml_text(kml_text)
    if not coords: return None, "No coordinates found in KML"
    if len(coords) == 1: return None, "Only one point found; need two points (start & end)"
    start = coords[0]; end = coords[1] if len(coords)>=2 else coords[-1]
    return (start, end), None

# OSRM routing
def route_osrm(start_lon, start_lat, end_lon, end_lat):
    url = f"https://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        raise Exception(f"OSRM error {r.status_code}: {r.text[:200]}")
    j = r.json()
    if 'routes' not in j or not j['routes']:
        raise Exception("OSRM returned no routes")
    coords = j['routes'][0]['geometry']['coordinates']
    return [[c[1], c[0]] for c in coords]

def parse_start_end_from_gmaps_url(url):
    m = re.search(r'/dir/(-?\d+\.\d+),(-?\d+\.\d+)/(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if m:
        lat1, lon1, lat2, lon2 = m.groups()
        return (float(lat1), float(lon1)), (float(lat2), float(lon2))
    pairs = re.findall(r'(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if len(pairs) >= 2:
        lat1, lon1 = pairs[0]; lat2, lon2 = pairs[-1]
        return (float(lat1), float(lon1)), (float(lat2), float(lon2))
    return None, None

# ---------- Endpoints ----------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/preview_links', methods=['POST'])
def preview_links():
    data = request.get_json(force=True)
    items = data.get('items', [])
    out = []
    for it in items:
        url = (it.get('url') or '').strip()
        name = it.get('name') or 'Path'
        color = (it.get('color') or '#FF0000').lstrip('#')
        if not url: continue
        se = parse_start_end_from_gmaps_url(url)
        if se == (None, None):
            return jsonify({'error': f'Cannot parse coordinates from URL: {url}'}), 400
        (lat1, lon1), (lat2, lon2) = se
        try:
            coords = route_osrm(lon1, lat1, lon2, lat2)
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        out.append({'name': name, 'color': color, 'coords': coords})
    return jsonify({'routes': out})

@app.route('/generate_multi', methods=['POST'])
def generate_multi():
    folder = request.form.get('folder_name', 'AUTO_FOLDER')
    try:
        count = int(request.form.get('count','0'))
    except:
        file_keys = [k for k in request.form.keys() if k.startswith('url_')]
        count = len(file_keys)
    placemarks = []
    for i in range(count):
        url = (request.form.get(f'url_{i}') or '').strip()
        name = (request.form.get(f'name_{i}') or f'Path_{i+1}').strip()
        color = (request.form.get(f'color_{i}') or 'FF0000').strip().lstrip('#')
        if not url: continue
        se = parse_start_end_from_gmaps_url(url)
        if se == (None, None):
            return jsonify({'error': f'Cannot parse coords for URL index {i}'}), 400
        (lat1, lon1), (lat2, lon2) = se
        try:
            coords = route_osrm(lon1, lat1, lon2, lat2)
        except Exception as e:
            return jsonify({'error': f'OSRM error index {i}: {str(e)}'}), 400
        kml_coords = " ".join(f"{pt[1]},{pt[0]},0" for pt in coords)
        kml_pm = f"""
        <Placemark>
          <name>{name}</name>
          <Style><LineStyle><color>ff{color[4:6] if len(color)==6 else '00'}{color[2:4] if len(color)==6 else '00'}{color[0:2] if len(color)==6 else 'ff'}</color><width>4</width></LineStyle></Style>
          <LineString><tessellate>1</tessellate><coordinates>{kml_coords}</coordinates></LineString>
        </Placemark>
        """
        placemarks.append(kml_pm)
    if not placemarks:
        return jsonify({'error': 'No valid routes generated'}), 400
    full_kml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <name>{folder}</name>
        {''.join(placemarks)}
      </Document>
    </kml>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('doc.kml', full_kml)
    buf.seek(0)
    return send_file(buf, mimetype='application/vnd.google-earth.kmz', as_attachment=True, download_name=(folder or 'multi_paths')+'.kmz')

@app.route('/preview_kmz', methods=['POST'])
def preview_kmz():
    try:
        count = int(request.form.get('count','0'))
    except:
        file_keys = [k for k in request.files.keys() if k.startswith('file_')]
        count = len(file_keys)
    out = []
    for i in range(count):
        fkey = f'file_{i}'
        file = request.files.get(fkey)
        if not file: continue
        file_stream = io.BytesIO(file.read())
        se, err = extract_two_points_from_kmz_file(file_stream)
        if err:
            return jsonify({'error': f'File {file.filename}: {err}'}), 400
        (start, end) = se
        start_lon, start_lat = start; end_lon, end_lat = end
        try:
            coords = route_osrm(start_lon, start_lat, end_lon, end_lat)
        except Exception as e:
            return jsonify({'error': f'OSRM error for file {file.filename}: {str(e)}'}), 400
        name = request.form.get(f'name_{i}', file.filename.rsplit('.',1)[0])
        color = (request.form.get(f'color_{i}', 'FF0000') or 'FF0000').lstrip('#')
        out.append({'name': name, 'color': color, 'coords': coords})
    return jsonify({'routes': out})

@app.route('/generate_kmz_upload', methods=['POST'])
def generate_kmz_upload():
    folder = request.form.get('folder_name', 'AUTO_FOLDER')
    try:
        count = int(request.form.get('count','0'))
    except:
        file_keys = [k for k in request.files.keys() if k.startswith('file_')]
        count = len(file_keys)
    placemarks = []
    for i in range(count):
        fkey = f'file_{i}'
        file = request.files.get(fkey)
        if not file: continue
        file_stream = io.BytesIO(file.read())
        se, err = extract_two_points_from_kmz_file(file_stream)
        if err:
            return jsonify({'error': f'File {file.filename}: {err}'}), 400
        (start, end) = se
        start_lon, start_lat = start; end_lon, end_lat = end
        try:
            coords = route_osrm(start_lon, start_lat, end_lon, end_lat)
        except Exception as e:
            return jsonify({'error': f'OSRM error for file {file.filename}: {str(e)}'},), 400
        name = request.form.get(f'name_{i}', file.filename.rsplit('.',1)[0])
        color = (request.form.get(f'color_{i}', 'FF0000') or 'FF0000').lstrip('#')
        kml_coords = " ".join(f"{pt[1]},{pt[0]},0" for pt in coords)
        kml_pm = f"""
        <Placemark>
          <name>{name}</name>
          <Style><LineStyle><color>ff{color[4:6] if len(color)==6 else '00'}{color[2:4] if len(color)==6 else '00'}{color[0:2] if len(color)==6 else 'ff'}</color><width>4</width></LineStyle></Style>
          <LineString><tessellate>1</tessellate><coordinates>{kml_coords}</coordinates></LineString>
        </Placemark>
        """
        placemarks.append(kml_pm)
    if not placemarks:
        return jsonify({'error': 'No valid routes generated'}), 400
    full_kml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Document>
        <name>{folder}</name>
        {''.join(placemarks)}
      </Document>
    </kml>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('doc.kml', full_kml)
    buf.seek(0)
    return send_file(buf, mimetype='application/vnd.google-earth.kmz', as_attachment=True, download_name=(folder or 'multi_paths_upload')+'.kmz')

if __name__ == '__main__':
    app.run(debug=True)
