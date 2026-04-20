# lAST GENERATOR

#!/usr/bin/env python3
# """
# ThoughtSpot Liveboard → Power BI PBIX Generator  v3 (updated)
# - Ensures Report/Layout is UTF-16LE with BOM and validates inner JSON strings.
# - Corrects Content_Types Override PartName entries (no leading slash).
# - Keeps DataMashup, DataModelSchema, rels, SecurityBindings, Version fixes.
# """

# import csv
# import io
# import json
# import os
# import re
# import struct
# import sys
# import uuid
# import zipfile
# from collections import defaultdict
# from datetime import datetime, timezone
# from pathlib import Path

# # -----------------------
# # Helpers
# # -----------------------
# def read_csv(path: Path) -> list:
#     if not path.exists():
#         print(f"[WARN] {path} not found – skipping")
#         return []
#     with open(path, newline="", encoding="utf-8-sig") as f:
#         return list(csv.DictReader(f))

# def short_guid() -> str:
#     return str(uuid.uuid4())

# # -----------------------
# # Chart-type mapping
# # -----------------------
# VIZ_TYPE_MAP = {
#     "LINE":                 "lineChart",
#     "COLUMN":               "columnChart",
#     "BAR":                  "barChart",
#     "PIE":                  "pieChart",
#     "AREA":                 "areaChart",
#     "LINE_STACKED_COLUMN":  "lineClusteredColumnComboChart",
#     "SCATTER":              "scatterChart",
#     "TABLE":                "tableEx",
#     "PIVOT_TABLE":          "pivotTable",
#     "KPI":                  "card",
# }

# def ts_to_pbi_type(ts_type: str) -> str:
#     return VIZ_TYPE_MAP.get(ts_type.strip().upper(), "lineChart")

# # -----------------------
# # Data loading
# # -----------------------
# def load_all(csv_dir: Path) -> dict:
#     data = {}
#     for name in ("liveboard_info", "data_sources", "columns",
#                  "joins", "visualizations", "formulas",
#                  "dax_measures", "parameters", "filters"):
#         data[name] = read_csv(csv_dir / f"{name}.csv")

#     tml_path = csv_dir / "raw_tml.json"
#     if tml_path.exists():
#         raw = json.loads(tml_path.read_text(encoding="utf-8"))
#         edoc = raw.get("edoc", "{}")
#         data["tml"] = json.loads(edoc) if isinstance(edoc, str) else edoc
#     else:
#         data["tml"] = {}
#     return data

# # -----------------------
# # Table schema (example)
# # -----------------------
# TABLE_SCHEMA = {
#     "sales_fact":     ["sale_id", "sale_date", "customer_id", "product_id",
#                        "quantity", "unit_price", "discount"],
#     "product_dim":    ["product_id", "product_name", "category", "unit_cost"],
#     "customer_dim":   ["customer_id", "customer_name", "loyalty_tier", "region"],
#     "inventory_fact": ["inventory_id", "inventory_date", "product_id",
#                        "opening_stock", "closing_stock", "stock_in", "stock_out"],
# }

# def collect_tables(data: dict) -> dict:
#     tables = {}
#     for row in data["data_sources"]:
#         t = row["source_name"].strip()
#         if t and t not in tables:
#             tables[t] = list(TABLE_SCHEMA.get(t, ["id"]))
#     for row in data["joins"]:
#         for key in ("left_table", "right_table"):
#             t = row[key].strip()
#             if t and t not in tables:
#                 tables[t] = list(TABLE_SCHEMA.get(t, ["id"]))
#     return tables

# # -----------------------
# # DAX sanitiser
# # -----------------------
# def sanitize_dax(ts_expr: str, primary_table: str) -> str:
#     expr = (ts_expr or "").strip()
#     if "=" in expr.split("\n")[0] and expr.index("=") < 60:
#         expr = expr[expr.index("=") + 1:].strip()

#     def replace_qualified(m):
#         alias = m.group(1)
#         col   = m.group(2)
#         base  = re.sub(r'_\d+$', '', alias)
#         return f"{base}[{col}]"

#     expr = re.sub(r'\[(\w+)::(\w+)\]', replace_qualified, expr)
#     expr = re.sub(r'\[([^\]]+)\]',
#                   lambda m: f"{primary_table}[{m.group(1)}]",
#                   expr)
#     expr = expr.replace("[Table]", "")

#     replacements = [
#         (r'\bunique\s+count\s*\(', 'DISTINCTCOUNT('),
#         (r'\bcount\s*\(',          'COUNT('),
#         (r'\bsum\s*\(',            'SUM('),
#         (r'\baverage\s*\(',        'AVERAGE('),
#         (r'\bmin\s*\(',            'MIN('),
#         (r'\bmax\s*\(',            'MAX('),
#         (r'\bif\s*\(',             'IF('),
#     ]
#     for pat, rep in replacements:
#         expr = re.sub(pat, rep, expr, flags=re.IGNORECASE)
#     return expr.strip()

# # -----------------------
# # DataMashup container
# # -----------------------
# def build_mashup_container(m_scripts: dict) -> bytes:
#     content_types = (
#         '<?xml version="1.0" encoding="utf-8"?>\n'
#         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
#         '  <Default Extension="xml" ContentType="text/xml"/>\n'
#         '  <Default Extension="m"   ContentType="application/vnd.ms-excel.requery"/>\n'
#         '</Types>'
#     )

#     relationships = (
#         '<?xml version="1.0" encoding="utf-8"?>\n'
#         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
#         '  <Relationship Type="http://schemas.microsoft.com/package/2018/11/connectionspackagerelationshiptype"'
#         ' Target="/Config/Package.xml" Id="R1"/>\n'
#         '  <Relationship Type="http://schemas.microsoft.com/package/2018/11/queriespackagerelationshiptype"'
#         ' Target="/Formulas/Section1.m" Id="R2"/>\n'
#         '</Relationships>'
#     )

#     package_xml = (
#         '<?xml version="1.0" encoding="utf-8"?>\n'
#         '<Package xmlns="http://schemas.microsoft.com/DataMashup"'
#         ' IsProductionMode="true">\n'
#         '  <MinClientVersion>2.9.0</MinClientVersion>\n'
#         '  <Culture>en-US</Culture>\n'
#         '</Package>'
#     )

#     lines = ["section Section1;\r\n"]
#     for name, m_expr in m_scripts.items():
#         safe = re.sub(r'[^A-Za-z0-9_]', '_', name)
#         lines.append(f'\r\nshared {safe} =\r\n{m_expr};\r\n')
#     section_m = "".join(lines)

#     buf = io.BytesIO()
#     with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
#         zf.writestr("[Content_Types].xml", content_types.encode("utf-8"))
#         zf.writestr("_rels/.rels",         relationships.encode("utf-8"))
#         zf.writestr("Config/Package.xml",  package_xml.encode("utf-8"))
#         zf.writestr("Formulas/Section1.m", section_m.encode("utf-8"))
#     inner = buf.getvalue()

#     # FIX: version=0 (not 6), permissions length = 0
#     return struct.pack("<II", 0, len(inner)) + inner + struct.pack("<I", 0)

# # -----------------------
# # M Power Query placeholder
# # -----------------------
# def m_placeholder(table_name: str, columns: list) -> str:
#     return (
#         f'let\n'
#         f'    Source = Sql.Database("localhost", "DataWarehouse"),\n'
#         f'    tbl = Source{{[Schema="dbo", Item="{table_name}"]}}[Data]\n'
#         f'in\n'
#         f'    tbl'
#     )

# import re
# import json
# from collections import defaultdict

# def collect_queryrefs_from_layout(layout_obj: dict) -> dict:
#     """
#     Return mapping: { table_name: set(column_or_measure_names) }
#     by scanning visualContainers' 'query' JSON and extracting 'Name' entries like 'table.col'.
#     """
#     refs = defaultdict(set)
#     sections = layout_obj.get("sections", [])
#     for sec in sections:
#         for vc in sec.get("visualContainers", []):
#             q = vc.get("query")
#             if not q:
#                 continue
#             try:
#                 qobj = json.loads(q)
#             except Exception:
#                 # if query is already a dict, handle it
#                 qobj = q if isinstance(q, dict) else None
#             if not qobj:
#                 continue
#             for sel in qobj.get("Select", []):
#                 nm = sel.get("Name")
#                 if not nm:
#                     continue
#                 # Expect format "table.field" (first dot splits table)
#                 if "." in nm:
#                     t, f = nm.split(".", 1)
#                     refs[t].add(f)
#     return refs



# # -----------------------
# # DataModelSchema (BIM JSON)
# # -----------------------
# def pbi_data_type_str(col_name: str) -> str:
#     c = (col_name or "").lower()
#     if "date" in c or c.endswith("_at"):
#         return "dateTime"
#     if c.endswith("_id") or c in ("quantity", "stock_in", "stock_out",
#                                    "opening_stock", "closing_stock"):
#         return "int64"
#     if c in ("unit_price", "discount", "unit_cost", "revenue", "profit"):
#         return "decimal"
#     return "string"

# def build_data_model_schema(data: dict, tables: dict) -> str:
#     relationships = []
#     seen = set()
#     fk_map = {
#         ("sales_fact",     "product_dim"):  ("product_id",  "product_id"),
#         ("sales_fact",     "customer_dim"): ("customer_id", "customer_id"),
#         ("inventory_fact", "product_dim"):  ("product_id",  "product_id"),
#     }
#     for j in data["joins"]:
#         lt = j["left_table"].strip()
#         rt = j["right_table"].strip()
#         key = (lt, rt)
#         if key in seen or lt not in tables or rt not in tables:
#             continue
#         seen.add(key)
#         from_col, to_col = fk_map.get(key, ("id", "id"))
#         relationships.append({
#             "name":                   f"rel_{lt}_{rt}",
#             "fromTable":              lt,
#             "fromColumn":             from_col,
#             "toTable":                rt,
#             "toColumn":               to_col,
#             "crossFilteringBehavior": "oneDirection",
#             "isActive":               True,
#         })

#     measures_by_table = defaultdict(list)
#     src_first = {}
#     for r in data["data_sources"]:
#         vid = r["viz_id"]
#         if vid not in src_first:
#             src_first[vid] = r["source_name"].strip()
#     for row in data["dax_measures"]:
#         vid  = row["viz_id"]
#         ptbl = src_first.get(vid, next(iter(tables), "sales_fact"))
#         raw  = row.get("ts_expression", row.get("dax_expression", ""))
#         dax  = sanitize_dax(raw, ptbl)
#         mname = row.get("formula_name", "Measure").strip()
#         measures_by_table[ptbl].append({"name": mname, "expression": dax})

#     bim_tables = []
#     for tname, cols in tables.items():
#         bim_cols = []
#         for col in cols:
#             bim_cols.append({
#                 "name":         col,
#                 "dataType":     pbi_data_type_str(col),
#                 "isNullable":   True,
#                 "sourceColumn": col,
#                 "summarizeBy":  "none",
#                 "isHidden":     False,
#                 "annotations":  [],
#             })

#         bim_measures = []
#         for m in measures_by_table.get(tname, []):
#             bim_measures.append({
#                 "name":         m["name"],
#                 "expression":   m["expression"],
#                 "formatString": "#,0.00",
#                 "isHidden":     False,
#             })

#         bim_tables.append({
#             "name":     tname,
#             "columns":  bim_cols,
#             "measures": bim_measures,
#             "partitions": [{
#                 "name":     tname,
#                 "dataView": "Full",
#                 "source": {
#                     "type":       "m",
#                     "expression": [
#                         "let",
#                         f"    Source = {re.sub(r'[^A-Za-z0-9_]', '_', tname)}",
#                         "in",
#                         "    Source"
#                     ]
#                 }
#             }],
#             "annotations": [],
#         })

#     schema = {
#         "name": "DataModel",
#         "compatibilityLevel": 1567,
#         "model": {
#             "culture":    "en-US",
#             "defaultPowerBIDataSourceVersion": "powerBI_V3",
#             "tables":          bim_tables,
#             "relationships":   relationships,
#             "annotations": [
#                 {"name": "PBIDesktopVersion",            "value": "2.136.1478.0"},
#                 {"name": "__PBI_TimeIntelligenceEnabled", "value": "1"},
#             ],
#         }
#     }
#     return json.dumps(schema, indent=2, ensure_ascii=False)

# # -----------------------
# # Layout builder (returns dict) with validation
# # -----------------------
# def build_layout(data: dict, lb_name: str) -> dict:
#     cols_per_viz = defaultdict(list)
#     for r in data["columns"]:
#         cname = r.get("column_name", "").strip()
#         if cname:
#             cols_per_viz[r["viz_id"]].append(cname)

#     src_per_viz = {}
#     for r in data["data_sources"]:
#         if r["viz_id"] not in src_per_viz:
#             src_per_viz[r["viz_id"]] = r["source_name"].strip()

#     measures_per_viz = defaultdict(list)
#     for r in data["dax_measures"]:
#         mname = r.get("formula_name", "").strip()
#         if mname:
#             measures_per_viz[r["viz_id"]].append(mname)

#     VIZ_W, VIZ_H, GAP, NCOLS = 612, 326, 16, 2
#     TOP_PAD = 10

#     visual_containers = []
#     for idx, vrow in enumerate(data["visualizations"]):
#         viz_id   = vrow["viz_id"]
#         viz_name = vrow.get("viz_name", "")
#         pbi_type = ts_to_pbi_type(vrow.get("viz_type", "LINE"))
#         tbl      = src_per_viz.get(viz_id, "sales_fact")
#         columns  = cols_per_viz.get(viz_id, [])
#         measures = measures_per_viz.get(viz_id, [])

#         col_x = (idx % NCOLS) * (VIZ_W + GAP)
#         col_y = TOP_PAD + (idx // NCOLS) * (VIZ_H + GAP)

#         vc = _make_visual_container(
#             viz_id=viz_id, viz_name=viz_name, pbi_type=pbi_type,
#             table_name=tbl, columns=columns, measures=measures,
#             x=col_x, y=col_y, w=VIZ_W, h=VIZ_H
#         )

#         # Validate that config/query/dataTransforms are valid JSON strings
#         for key in ("config", "query", "dataTransforms"):
#             try:
#                 json.loads(vc[key])
#             except Exception as e:
#                 raise ValueError(f"Invalid JSON in visual {viz_id} field {key}: {e}")

#         visual_containers.append(vc)

#     section = {
#         "id":               0,
#         "name":             "ReportSection",
#         "displayName":      "Page 1",
#         "filters":          "[]",
#         "ordinal":          0,
#         "visualContainers": visual_containers,
#         "config": json.dumps({
#             "relationships": [], 
#             "sections": [{"id": 0, "filterConfig": {"type": "Legacy"}}]
#         }),
#         "displayOption": 1,
#         "width":         1280,
#         "height":        720,
#     }

#     layout = {
#         "id": 0,
#         "resourcePackages": [{
#             "resourcePackage": {
#                 "name":     "SharedResources",
#                 "type":     2,
#                 "items":    [{"type": 202, "path": "BaseThemes/CY24SU10.json", "name": "CY24SU10"}],
#                 "disabled": False,
#             }
#         }],
#         "sections": [section],
#         "config": json.dumps({
#             "version": "5.51",
#             "themeCollection": {
#                 "baseTheme": {"name": "CY24SU10", "version": "5.51", "type": 2}
#             }
#         }),
#         "layoutOptimization": 0,
#     }
#     return layout

# def _make_visual_container(
#     viz_id, viz_name, pbi_type, table_name,
#     columns, measures, x, y, w, h
# ) -> dict:
#     alias = re.sub(r'[^a-z]', '', table_name.lower())[:4] or "tbl"

#     from_clause = [{"Name": alias, "Entity": table_name, "Type": 0}]

#     select_clause = []
#     for col in columns:
#         select_clause.append({
#             "Column": {
#                 "Expression": {"SourceRef": {"Source": alias}},
#                 "Property":   col,
#             },
#             "Name": f"{table_name}.{col}",
#         })
#     for m in measures:
#         select_clause.append({
#             "Measure": {
#                 "Expression": {"SourceRef": {"Source": alias}},
#                 "Property":   m,
#             },
#             "Name": f"{table_name}.{m}",
#         })

#     proto_query = {
#         "Version": 2,
#         "From":    from_clause,
#         "Select":  select_clause,
#         "OrderBy": [],
#     }

#     projections = _build_projections(pbi_type, table_name, columns, measures)

#     dt_meta_select = [
#         {
#             "queryName":   f"{table_name}.{f}",
#             "displayName": f,
#             "queryRef":    f"{table_name}.{f}",
#         }
#         for f in (columns + measures)
#     ]
#     data_transforms = {
#         "queryMetadata":  {"Select": dt_meta_select, "Filters": []},
#         "visualElements": [{"id": 0, "groups": [], "pivots": []}],
#         "projections":    projections,
#         "roles":          {},
#     }

#     config_obj = {
#         "name": viz_id,
#         "layouts": [{
#             "id":       0,
#             "position": {"x": x, "y": y, "z": 0, "width": w, "height": h},
#         }],
#         "singleVisual": {
#             "visualType":              pbi_type,
#             "drillFilterOtherVisuals": True,
#             "hasDefaultSort":          True,
#             "displayName":             viz_name,
#             "projectedActiveUserDefinedHierarchies": [],
#             "prototypeQuery": proto_query,          # must be INSIDE singleVisual
#             "vcObjects": {
#                 "title": [{
#                     "properties": {
#                         "show": {"expr": {"Literal": {"Value": "true"}}},
#                         "text": {"expr": {"Literal": {"Value": f"{viz_name}"}}}
#                     }
#                 }]
#             },
#         },
#     }

#     return {
#         "x":              x,
#         "y":              y,
#         "z":              0,
#         "width":          w,
#         "height":         h,
#         "config":         json.dumps(config_obj, ensure_ascii=False),
#         "filters":        "[]",
#         "query":          json.dumps(proto_query, ensure_ascii=False),
#         "dataTransforms": json.dumps(data_transforms, ensure_ascii=False),
#     }

# def _build_projections(pbi_type, table_name, columns, measures) -> dict:
#     proj = {}
#     all_fields = [f"{table_name}.{c}" for c in columns] + \
#                  [f"{table_name}.{m}" for m in measures]

#     def ref(qname):
#         return {"queryRef": qname, "active": True}

#     if pbi_type in ("tableEx", "pivotTable"):
#         proj["Values"] = [ref(f) for f in all_fields]
#     elif pbi_type == "card":
#         proj["Values"] = [ref(f) for f in all_fields[:1]]
#     elif pbi_type == "pieChart":
#         if all_fields:
#             proj["Category"] = [ref(all_fields[0])]
#         if len(all_fields) > 1:
#             proj["Y"] = [ref(f) for f in all_fields[1:]]
#     elif pbi_type == "scatterChart":
#         if all_fields:
#             proj["Details"] = [ref(all_fields[0])]
#         if len(all_fields) > 1:
#             proj["X"] = [ref(all_fields[1])]
#         if len(all_fields) > 2:
#             proj["Y"] = [ref(all_fields[2])]
#     else:
#         if all_fields:
#             proj["Category"] = [ref(all_fields[0])]
#         if len(all_fields) > 1:
#             proj["Y"] = [ref(f) for f in all_fields[1:]]
#     return proj

# # -----------------------
# # Content Types (FIX: no leading slash in Override PartName)
# # -----------------------
# def build_content_types_xml() -> str:
#     return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
# <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
#   <Default Extension="json" ContentType="application/json"/>
#   <Default Extension="xml"  ContentType="application/xml"/>
#   <Default Extension="m"    ContentType="application/vnd.ms-excel.requery"/>
#   <Override PartName="Report/Layout" ContentType="application/json"/>
#   <Override PartName="DataMashup" ContentType="application/octet-stream"/>
#   <Override PartName="DataModelSchema" ContentType="application/json"/>
#   <Override PartName="Settings" ContentType="application/json"/>
#   <Override PartName="Metadata" ContentType="application/json"/>
#   <Override PartName="SecurityBindings" ContentType="application/octet-stream"/>
#   <Override PartName="Version" ContentType="text/plain"/>
#   <Override PartName="_rels/.rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
#   <Override PartName="docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>
# </Types>
# """

# # -----------------------
# # _rels/.rels (FIX: correct URIs)
# # -----------------------
# RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
# <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
#   <Relationship Id="rId1"
#     Type="http://schemas.microsoft.com/DataMashup"
#     Target="DataMashup"/>
#   <Relationship Id="rId2"
#     Type="http://schemas.microsoft.com/power-bi/2018/report"
#     Target="Report/Layout"/>
# </Relationships>
# """

# THEME_JSON = json.dumps({
#     "name": "CY24SU10",
#     "dataColors": [
#         "#118DFF","#12239E","#E66C37","#6B007B",
#         "#E044A7","#744EC2","#D9B300","#D64550"
#     ],
#     "background":  "#FFFFFF",
#     "foreground":  "#252423",
#     "tableAccent": "#118DFF",
# }, indent=2)

# SETTINGS_JSON = json.dumps({
#     "isPersistentUserStateDisabled":    False,
#     "hideVisualContainerHeader":        False,
#     "useStylableVisualContainerHeader": True,
#     "exportDataRestriction":            "AllowAggregatedData",
#     "reportAccessibilityMode":          "fullKeyboardAccess",
# }, indent=2)

# def make_metadata_json(lb_name: str) -> str:
#     return json.dumps({
#         "version":            "5.51",
#         "createdFromTemplate": "",
#         "defaultTheme":       "SharedResources/BaseThemes/CY24SU10.json",
#         "ownerId":            "",
#         "reportId":           str(uuid.uuid4()),
#         "lastModifiedTime":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
#     }, indent=2)

# def make_custom_xml(lb_name: str) -> str:
#     now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
#     return (
#         '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
#         '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"\n'
#         '            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">\n'
#         f'  <property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="2" name="ReportName">\n'
#         f'    <vt:lpwstr>{lb_name}</vt:lpwstr>\n'
#         f'  </property>\n'
#         f'  <property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="3" name="CreatedDate">\n'
#         f'    <vt:lpwstr>{now}</vt:lpwstr>\n'
#         f'  </property>\n'
#         f'  <property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="4" name="GeneratedBy">\n'
#         f'    <vt:lpwstr>ts_to_pbix_v3_fixed.py</vt:lpwstr>\n'
#         f'  </property>\n'
#         '</Properties>\n'
#     )

# # -----------------------
# # Main assembler
# # -----------------------
# def build_pbix(data: dict, out_path: Path):
#     lb_info = data["liveboard_info"][0] if data["liveboard_info"] else {}
#     lb_name = lb_info.get("liveboard_name", "GeneratedReport")

#     tables = collect_tables(data)
#     print(f"[INFO] Tables         : {list(tables.keys())}")
#     print(f"[INFO] Visualizations : {len(data['visualizations'])}")
#     print(f"[INFO] DAX measures   : {len(data['dax_measures'])}")

#     m_scripts    = {t: m_placeholder(t, cols) for t, cols in tables.items()}
#     layout_obj   = build_layout(data, lb_name)  # dict
#         # after layout_obj = build_layout(...)
#     layout_obj   = build_layout(data, lb_name)            # dict
#     # collect queryRefs from layout and ensure tables include those fields
#     queryrefs = collect_queryrefs_from_layout(layout_obj)
#     # 'tables' is produced by collect_tables(data)
#     tables = collect_tables(data)

#     # Ensure each table in queryrefs exists in tables and add missing columns
#     for tname, fields in queryrefs.items():
#         if tname not in tables:
#             # create table entry with empty list so BIM will include it
#             tables[tname] = []
#         # add any missing fields to the table's column list
#         for f in fields:
#             if f not in tables[tname]:
#                  tables[tname].append(f)

#     mashup_bytes = build_mashup_container(m_scripts)
#     schema_json  = build_data_model_schema(data, tables)

#     # Serialize outer layout JSON once and ensure valid JSON
#     layout_json = json.dumps(layout_obj, ensure_ascii=False, separators=(",", ":"), indent=2)

#     # Ensure UTF-16LE with BOM
#     layout_bytes = b"\xff\xfe" + layout_json.encode("utf-16-le")

#     # Optional safety check before writing: decode back and parse
#     try:
#         decoded = layout_bytes[2:].decode("utf-16-le")
#         json.loads(decoded)
#     except Exception as e:
#         raise RuntimeError(f"Layout JSON validation failed after encoding: {e}")

#     with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
#         # FIX: Use corrected Content_Types and rels
#         zf.writestr("[Content_Types].xml", build_content_types_xml().encode("utf-8"))
#         zf.writestr("_rels/.rels",         RELS_XML.encode("utf-8"))
#         zf.writestr("docProps/custom.xml", make_custom_xml(lb_name).encode("utf-8"))

#         # Report
#         zf.writestr("Report/Layout", layout_bytes)
#         zf.writestr(
#             "Report/StaticResources/SharedResources/BaseThemes/CY24SU10.json",
#             THEME_JSON.encode("utf-8")
#         )

#         # Data layer
#         zf.writestr("DataMashup",      mashup_bytes)

#         # DataModelSchema required in PBI 2.130+
#         zf.writestr("DataModelSchema", schema_json.encode("utf-8"))

#         # Supporting files
#         zf.writestr("Metadata",        make_metadata_json(lb_name).encode("utf-8"))
#         zf.writestr("Settings",        SETTINGS_JSON.encode("utf-8"))

#         # SecurityBindings must be a 4-byte zero (empty length prefix)
#         zf.writestr("SecurityBindings", struct.pack("<I", 0))

#         # Version is plain ASCII, no BOM
#         zf.writestr("Version", b"3.0")

#     size_kb = out_path.stat().st_size // 1024
#     print(f"\n✅  PBIX written → {out_path.resolve()}  ({size_kb} KB)\n")

# # -----------------------
# # CLI entrypoint
# # -----------------------
# def main(argv):
#     if len(argv) < 3:
#         print("Usage: python ts_to_pbix_v3_fixed.py <csv_dir> <out.pbix>")
#         sys.exit(2)
#     csv_dir = Path(argv[1])
#     out_pbix = Path(argv[2])
#     data = load_all(csv_dir)
#     build_pbix(data, out_pbix)

# if __name__ == "__main__":
#     main(sys.argv)
#!/usr/bin/env python3
"""
ThoughtSpot Liveboard → Power BI PBIX Generator  v3 (updated)
- Ensures Report/Layout is UTF-16LE with BOM and validates inner JSON strings.
- Ensures DataModelSchema (BIM JSON) contains every field referenced by Report/Layout:
  - Columns referenced as "table.column" are added as table columns.
  - Measures referenced as "Measure" entries are added as BIM measures.
- Corrects Content_Types Override PartName entries (no leading slash).
- Keeps DataMashup, DataModelSchema, rels, SecurityBindings, Version fixes.
- Includes a validator/repair helper and CLI entrypoint.
"""

import csv
import io
import json
import os
import re
import struct
import sys
import uuid
import zipfile
import tempfile
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# -----------------------
# Helpers
# -----------------------
def read_csv(path: Path) -> list:
    if not path.exists():
        print(f"[WARN] {path} not found – skipping")
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def short_guid() -> str:
    return str(uuid.uuid4())

# -----------------------
# Chart-type mapping
# -----------------------
VIZ_TYPE_MAP = {
    "LINE":                 "lineChart",
    "COLUMN":               "columnChart",
    "BAR":                  "barChart",
    "PIE":                  "pieChart",
    "AREA":                 "areaChart",
    "LINE_STACKED_COLUMN":  "lineClusteredColumnComboChart",
    "SCATTER":              "scatterChart",
    "TABLE":                "tableEx",
    "PIVOT_TABLE":          "pivotTable",
    "KPI":                  "card",
}

def ts_to_pbi_type(ts_type: str) -> str:
    return VIZ_TYPE_MAP.get((ts_type or "").strip().upper(), "lineChart")

# -----------------------
# Data loading
# -----------------------
def load_all(csv_dir: Path) -> dict:
    data = {}
    for name in ("liveboard_info", "data_sources", "columns",
                 "joins", "visualizations", "formulas",
                 "dax_measures", "parameters", "filters"):
        data[name] = read_csv(csv_dir / f"{name}.csv")

    tml_path = csv_dir / "raw_tml.json"
    if tml_path.exists():
        raw = json.loads(tml_path.read_text(encoding="utf-8"))
        edoc = raw.get("edoc", "{}")
        data["tml"] = json.loads(edoc) if isinstance(edoc, str) else edoc
    else:
        data["tml"] = {}
    return data

# -----------------------
# Table schema (example)
# -----------------------
TABLE_SCHEMA = {
    "sales_fact":     ["sale_id", "sale_date", "customer_id", "product_id",
                       "quantity", "unit_price", "discount"],
    "product_dim":    ["product_id", "product_name", "category", "unit_cost"],
    "customer_dim":   ["customer_id", "customer_name", "loyalty_tier", "region"],
    "inventory_fact": ["inventory_id", "inventory_date", "product_id",
                       "opening_stock", "closing_stock", "stock_in", "stock_out"],
}

def collect_tables(data: dict) -> dict:
    tables = {}
    for row in data.get("data_sources", []):
        t = (row.get("source_name") or "").strip()
        if t and t not in tables:
            tables[t] = list(TABLE_SCHEMA.get(t, ["id"]))
    for row in data.get("joins", []):
        for key in ("left_table", "right_table"):
            t = (row.get(key) or "").strip()
            if t and t not in tables:
                tables[t] = list(TABLE_SCHEMA.get(t, ["id"]))
    return tables

# -----------------------
# DAX sanitiser
# -----------------------
def sanitize_dax(ts_expr: str, primary_table: str) -> str:
    expr = (ts_expr or "").strip()
    if "=" in expr.split("\n")[0] and expr.index("=") < 60:
        expr = expr[expr.index("=") + 1:].strip()

    def replace_qualified(m):
        alias = m.group(1)
        col   = m.group(2)
        base  = re.sub(r'_\d+$', '', alias)
        return f"{base}[{col}]"

    expr = re.sub(r'\[(\w+)::(\w+)\]', replace_qualified, expr)
    expr = re.sub(r'\[([^\]]+)\]',
                  lambda m: f"{primary_table}[{m.group(1)}]",
                  expr)
    expr = expr.replace("[Table]", "")

    replacements = [
        (r'\bunique\s+count\s*\(', 'DISTINCTCOUNT('),
        (r'\bcount\s*\(',          'COUNT('),
        (r'\bsum\s*\(',            'SUM('),
        (r'\baverage\s*\(',        'AVERAGE('),
        (r'\bmin\s*\(',            'MIN('),
        (r'\bmax\s*\(',            'MAX('),
        (r'\bif\s*\(',             'IF('),
    ]
    for pat, rep in replacements:
        expr = re.sub(pat, rep, expr, flags=re.IGNORECASE)
    return expr.strip()

# -----------------------
# DataMashup container
# -----------------------
def build_mashup_container(m_scripts: dict) -> bytes:
    content_types = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Default Extension="xml" ContentType="text/xml"/>\n'
        '  <Default Extension="m"   ContentType="application/vnd.ms-excel.requery"/>\n'
        '</Types>'
    )

    relationships = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Type="http://schemas.microsoft.com/package/2018/11/connectionspackagerelationshiptype"'
        ' Target="/Config/Package.xml" Id="R1"/>\n'
        '  <Relationship Type="http://schemas.microsoft.com/package/2018/11/queriespackagerelationshiptype"'
        ' Target="/Formulas/Section1.m" Id="R2"/>\n'
        '</Relationships>'
    )

    package_xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Package xmlns="http://schemas.microsoft.com/DataMashup"'
        ' IsProductionMode="true">\n'
        '  <MinClientVersion>2.9.0</MinClientVersion>\n'
        '  <Culture>en-US</Culture>\n'
        '</Package>'
    )

    lines = ["section Section1;\r\n"]
    for name, m_expr in m_scripts.items():
        safe = re.sub(r'[^A-Za-z0-9_]', '_', name)
        lines.append(f'\r\nshared {safe} =\r\n{m_expr};\r\n')
    section_m = "".join(lines)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types.encode("utf-8"))
        zf.writestr("_rels/.rels",         relationships.encode("utf-8"))
        zf.writestr("Config/Package.xml",  package_xml.encode("utf-8"))
        zf.writestr("Formulas/Section1.m", section_m.encode("utf-8"))
    inner = buf.getvalue()

    # FIX: version=0 (not 6), permissions length = 0
    return struct.pack("<II", 0, len(inner)) + inner + struct.pack("<I", 0)

# -----------------------
# M Power Query placeholder
# -----------------------
def m_placeholder(table_name: str, columns: list) -> str:
    return (
        f'let\n'
        f'    Source = Sql.Database("localhost", "DataWarehouse"),\n'
        f'    tbl = Source{{[Schema="dbo", Item="{table_name}"]}}[Data]\n'
        f'in\n'
        f'    tbl'
    )

# -----------------------
# DataModelSchema (BIM JSON)
# -----------------------
def pbi_data_type_str(col_name: str) -> str:
    c = (col_name or "").lower()
    if "date" in c or c.endswith("_at"):
        return "dateTime"
    if c.endswith("_id") or c in ("quantity", "stock_in", "stock_out",
                                   "opening_stock", "closing_stock"):
        return "int64"
    if c in ("unit_price", "discount", "unit_cost", "revenue", "profit"):
        return "decimal"
    return "string"

def build_data_model_schema(data: dict, tables: dict, extra_measures_by_table: dict = None) -> str:
    """
    Build BIM JSON. `tables` is mapping table_name -> list(columns).
    `extra_measures_by_table` is mapping table_name -> list of measure dicts {"name":..., "expression":...}
    """
    # Relationships
    relationships = []
    seen = set()
    fk_map = {
        ("sales_fact",     "product_dim"):  ("product_id",  "product_id"),
        ("sales_fact",     "customer_dim"): ("customer_id", "customer_id"),
        ("inventory_fact", "product_dim"):  ("product_id",  "product_id"),
    }
    for j in data.get("joins", []):
        lt = (j.get("left_table") or "").strip()
        rt = (j.get("right_table") or "").strip()
        key = (lt, rt)
        if key in seen or lt not in tables or rt not in tables:
            continue
        seen.add(key)
        from_col, to_col = fk_map.get(key, ("id", "id"))
        relationships.append({
            "name":                   f"rel_{lt}_{rt}",
            "fromTable":              lt,
            "fromColumn":             from_col,
            "toTable":                rt,
            "toColumn":               to_col,
            "crossFilteringBehavior": "oneDirection",
            "isActive":               True,
        })

    # Measures grouped by primary table (from CSV)
    measures_by_table = defaultdict(list)
    src_first = {}
    for r in data.get("data_sources", []):
        vid = r.get("viz_id")
        if vid not in src_first:
            src_first[vid] = (r.get("source_name") or "").strip()
    for row in data.get("dax_measures", []):
        vid  = row.get("viz_id")
        ptbl = src_first.get(vid, next(iter(tables), "sales_fact"))
        raw  = row.get("ts_expression", row.get("dax_expression", "") or "")
        dax  = sanitize_dax(raw, ptbl)
        mname = (row.get("formula_name") or "Measure").strip()
        measures_by_table[ptbl].append({"name": mname, "expression": dax})

    # Merge extra measures discovered from layout
    if extra_measures_by_table:
        for tname, mlist in extra_measures_by_table.items():
            for m in mlist:
                # avoid duplicates by name
                if not any(existing.get("name") == m.get("name") for existing in measures_by_table.get(tname, [])):
                    measures_by_table[tname].append(m)

    # BIM tables
    bim_tables = []
    for tname, cols in tables.items():
        bim_cols = []
        for col in cols:
            bim_cols.append({
                "name":         col,
                "dataType":     pbi_data_type_str(col),
                "isNullable":   True,
                "sourceColumn": col,
                "summarizeBy":  "none",
                "isHidden":     False,
                "annotations":  [],
            })

        bim_measures = []
        for m in measures_by_table.get(tname, []):
            bim_measures.append({
                "name":         m["name"],
                "expression":   m["expression"],
                "formatString": "#,0.00",
                "isHidden":     False,
            })

        bim_tables.append({
            "name":     tname,
            "columns":  bim_cols,
            "measures": bim_measures,
            "partitions": [{
                "name":     tname,
                "dataView": "Full",
                "source": {
                    "type":       "m",
                    "expression": [
                        "let",
                        f"    Source = {re.sub(r'[^A-Za-z0-9_]', '_', tname)}",
                        "in",
                        "    Source"
                    ]
                }
            }],
            "annotations": [],
        })

    schema = {
        "name": "DataModel",
        "compatibilityLevel": 1567,
        "model": {
            "culture":    "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "tables":          bim_tables,
            "relationships":   relationships,
            "annotations": [
                {"name": "PBIDesktopVersion",            "value": "2.136.1478.0"},
                {"name": "__PBI_TimeIntelligenceEnabled", "value": "1"},
            ],
        }
    }
    return json.dumps(schema, indent=2, ensure_ascii=False)

# -----------------------
# Layout builder (returns dict) with validation
# -----------------------
def build_layout(data: dict, lb_name: str) -> dict:
    cols_per_viz = defaultdict(list)
    for r in data.get("columns", []):
        cname = (r.get("column_name") or "").strip()
        if cname:
            cols_per_viz[r.get("viz_id")].append(cname)

    src_per_viz = {}
    for r in data.get("data_sources", []):
        if r.get("viz_id") not in src_per_viz:
            src_per_viz[r.get("viz_id")] = (r.get("source_name") or "").strip()

    measures_per_viz = defaultdict(list)
    for r in data.get("dax_measures", []):
        mname = (r.get("formula_name") or "").strip()
        if mname:
            measures_per_viz[r.get("viz_id")].append(mname)

    VIZ_W, VIZ_H, GAP, NCOLS = 612, 326, 16, 2
    TOP_PAD = 10

    visual_containers = []
    for idx, vrow in enumerate(data.get("visualizations", [])):
        viz_id   = vrow.get("viz_id")
        viz_name = vrow.get("viz_name", "")
        pbi_type = ts_to_pbi_type(vrow.get("viz_type", "LINE"))
        tbl      = src_per_viz.get(viz_id, "sales_fact")
        columns  = cols_per_viz.get(viz_id, [])
        measures = measures_per_viz.get(viz_id, [])

        col_x = (idx % NCOLS) * (VIZ_W + GAP)
        col_y = TOP_PAD + (idx // NCOLS) * (VIZ_H + GAP)

        vc = _make_visual_container(
            viz_id=viz_id, viz_name=viz_name, pbi_type=pbi_type,
            table_name=tbl, columns=columns, measures=measures,
            x=col_x, y=col_y, w=VIZ_W, h=VIZ_H
        )

        # Validate that config/query/dataTransforms are valid JSON strings
        for key in ("config", "query", "dataTransforms"):
            try:
                json.loads(vc[key])
            except Exception as e:
                raise ValueError(f"Invalid JSON in visual {viz_id} field {key}: {e}")

        visual_containers.append(vc)

    section = {
        "id":               0,
        "name":             "ReportSection",
        "displayName":      "Page 1",
        "filters":          "[]",
        "ordinal":          0,
        "visualContainers": visual_containers,
        "config": json.dumps({
            "relationships": [], 
            "sections": [{"id": 0, "filterConfig": {"type": "Legacy"}}]
        }),
        "displayOption": 1,
        "width":         1280,
        "height":        720,
    }

    layout = {
        "id": 0,
        "resourcePackages": [{
            "resourcePackage": {
                "name":     "SharedResources",
                "type":     2,
                "items":    [{"type": 202, "path": "BaseThemes/CY24SU10.json", "name": "CY24SU10"}],
                "disabled": False,
            }
        }],
        "sections": [section],
        "config": json.dumps({
            "version": "5.51",
            "themeCollection": {
                "baseTheme": {"name": "CY24SU10", "version": "5.51", "type": 2}
            }
        }),
        "layoutOptimization": 0,
    }
    return layout

def _make_visual_container(
    viz_id, viz_name, pbi_type, table_name,
    columns, measures, x, y, w, h
) -> dict:
    alias = re.sub(r'[^a-z]', '', (table_name or "").lower())[:4] or "tbl"

    from_clause = [{"Name": alias, "Entity": table_name, "Type": 0}]

    select_clause = []
    for col in columns:
        select_clause.append({
            "Column": {
                "Expression": {"SourceRef": {"Source": alias}},
                "Property":   col,
            },
            "Name": f"{table_name}.{col}",
        })
    for m in measures:
        # treat measures as Measure entries in the query
        select_clause.append({
            "Measure": {
                "Expression": {"SourceRef": {"Source": alias}},
                "Property":   m,
            },
            "Name": f"{table_name}.{m}",
        })

    proto_query = {
        "Version": 2,
        "From":    from_clause,
        "Select":  select_clause,
        "OrderBy": [],
    }

    projections = _build_projections(pbi_type, table_name, columns, measures)

    dt_meta_select = [
        {
            "queryName":   f"{table_name}.{f}",
            "displayName": f,
            "queryRef":    f"{table_name}.{f}",
        }
        for f in (columns + measures)
    ]
    data_transforms = {
        "queryMetadata":  {"Select": dt_meta_select, "Filters": []},
        "visualElements": [{"id": 0, "groups": [], "pivots": []}],
        "projections":    projections,
        "roles":          {},
    }

    config_obj = {
        "name": viz_id,
        "layouts": [{
            "id":       0,
            "position": {"x": x, "y": y, "z": 0, "width": w, "height": h},
        }],
        "singleVisual": {
            "visualType":              pbi_type,
            "drillFilterOtherVisuals": True,
            "hasDefaultSort":          True,
            "displayName":             viz_name,
            "projectedActiveUserDefinedHierarchies": [],
            "prototypeQuery": proto_query,          # must be INSIDE singleVisual
            "vcObjects": {
                "title": [{
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "text": {"expr": {"Literal": {"Value": f"{viz_name}"}}}
                    }
                }]
            },
        },
    }

    return {
        "x":              x,
        "y":              y,
        "z":              0,
        "width":          w,
        "height":         h,
        "config":         json.dumps(config_obj, ensure_ascii=False),
        "filters":        "[]",
        "query":          json.dumps(proto_query, ensure_ascii=False),
        "dataTransforms": json.dumps(data_transforms, ensure_ascii=False),
    }

def _build_projections(pbi_type, table_name, columns, measures) -> dict:
    proj = {}
    all_fields = [f"{table_name}.{c}" for c in columns] + \
                 [f"{table_name}.{m}" for m in measures]

    def ref(qname):
        return {"queryRef": qname, "active": True}

    if pbi_type in ("tableEx", "pivotTable"):
        proj["Values"] = [ref(f) for f in all_fields]
    elif pbi_type == "card":
        proj["Values"] = [ref(f) for f in all_fields[:1]]
    elif pbi_type == "pieChart":
        if all_fields:
            proj["Category"] = [ref(all_fields[0])]
        if len(all_fields) > 1:
            proj["Y"] = [ref(f) for f in all_fields[1:]]
    elif pbi_type == "scatterChart":
        if all_fields:
            proj["Details"] = [ref(all_fields[0])]
        if len(all_fields) > 1:
            proj["X"] = [ref(all_fields[1])]
        if len(all_fields) > 2:
            proj["Y"] = [ref(all_fields[2])]
    else:
        if all_fields:
            proj["Category"] = [ref(all_fields[0])]
        if len(all_fields) > 1:
            proj["Y"] = [ref(f) for f in all_fields[1:]]
    return proj

# -----------------------
# Content Types (FIX: no leading slash in Override PartName)
# -----------------------
def build_content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="xml"  ContentType="application/xml"/>
  <Default Extension="m"    ContentType="application/vnd.ms-excel.requery"/>
  <Override PartName="/Report/Layout" ContentType="application/json"/>
  <Override PartName="/DataMashup" ContentType="application/octet-stream"/>
  <Override PartName="/DataModelSchema" ContentType="application/json"/>
  <Override PartName="/Settings" ContentType="application/json"/>
  <Override PartName="/Metadata" ContentType="application/json"/>
  <Override PartName="/SecurityBindings" ContentType="application/octet-stream"/>
  <Override PartName="/Version" ContentType="text/plain"/>
  <Override PartName="/_rels/.rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>
</Types>
"""

# -----------------------
# _rels/.rels (FIX: correct URIs)
# -----------------------
RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.microsoft.com/DataMashup"
    Target="/DataMashup"/>
  <Relationship Id="rId2"
    Type="http://schemas.microsoft.com/power-bi/2018/report"
    Target="/Report/Layout"/>
</Relationships>
"""

THEME_JSON = json.dumps({
    "name": "CY24SU10",
    "dataColors": [
        "#118DFF","#12239E","#E66C37","#6B007B",
        "#E044A7","#744EC2","#D9B300","#D64550"
    ],
    "background":  "#FFFFFF",
    "foreground":  "#252423",
    "tableAccent": "#118DFF",
}, indent=2)

SETTINGS_JSON = json.dumps({
    "isPersistentUserStateDisabled":    False,
    "hideVisualContainerHeader":        False,
    "useStylableVisualContainerHeader": True,
    "exportDataRestriction":            "AllowAggregatedData",
    "reportAccessibilityMode":          "fullKeyboardAccess",
}, indent=2)

def make_metadata_json(lb_name: str) -> str:
    return json.dumps({
        "version":            "5.51",
        "createdFromTemplate": "",
        "defaultTheme":       "SharedResources/BaseThemes/CY24SU10.json",
        "ownerId":            "",
        "reportId":           str(uuid.uuid4()),
        "lastModifiedTime":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }, indent=2)

def make_custom_xml(lb_name: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"\n'
        '            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">\n'
        f'  <property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="2" name="ReportName">\n'
        f'    <vt:lpwstr>{lb_name}</vt:lpwstr>\n'
        f'  </property>\n'
        f'  <property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="3" name="CreatedDate">\n'
        f'    <vt:lpwstr>{now}</vt:lpwstr>\n'
        f'  </property>\n'
        f'  <property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="4" name="GeneratedBy">\n'
        f'    <vt:lpwstr>ts_to_pbix_v3_fixed.py</vt:lpwstr>\n'
        f'  </property>\n'
        '</Properties>\n'
    )

# -----------------------
# Helpers to extract queryRefs and measures from layout object
# -----------------------
def collect_queryrefs_from_layout(layout_obj: dict):
    """
    Return mapping:
      - columns_by_table: { table_name: set(column_names) }
      - measures_by_table: { table_name: [ {name:..., expression: "/* placeholder */"} ] }
    by scanning visualContainers' 'query' JSON and extracting 'Name' entries and whether they are Column or Measure.
    """
    columns_by_table = defaultdict(set)
    measures_by_table = defaultdict(list)

    sections = layout_obj.get("sections", [])
    for sec in sections:
        for vc in sec.get("visualContainers", []):
            q = vc.get("query")
            if not q:
                continue
            try:
                qobj = json.loads(q)
            except Exception:
                # if query is already a dict, handle it
                qobj = q if isinstance(q, dict) else None
            if not qobj:
                continue
            for sel in qobj.get("Select", []):
                # Column entry
                if "Column" in sel:
                    nm = sel.get("Name")
                    if nm and "." in nm:
                        t, f = nm.split(".", 1)
                        columns_by_table[t].add(f)
                # Measure entry
                elif "Measure" in sel:
                    nm = sel.get("Name")
                    if nm and "." in nm:
                        t, f = nm.split(".", 1)
                        # Add as a measure with a placeholder expression (Power BI will accept a measure expression; we use a simple SUM fallback)
                        # If you have a DAX expression available elsewhere, you can replace the placeholder.
                        measures_by_table[t].append({"name": f, "expression": f"SUM('{t}'[{f}])"})
    return columns_by_table, measures_by_table

# -----------------------
# Main assembler
# -----------------------
def build_pbix(data: dict, out_path: Path):
    lb_info = data.get("liveboard_info", [{}])[0] if data.get("liveboard_info") else {}
    lb_name = lb_info.get("liveboard_name", "GeneratedReport")

    # Build layout object first (dict)
    layout_obj   = build_layout(data, lb_name)            # dict

    # Collect tables from data and joins
    tables = collect_tables(data)

    # Collect queryRefs and measures from layout and ensure tables include those fields
    cols_refs, measures_refs = collect_queryrefs_from_layout(layout_obj)
    for tname, cols in cols_refs.items():
        if tname not in tables:
            tables[tname] = []
        for c in cols:
            if c not in tables[tname]:
                tables[tname].append(c)

    # Prepare extra measures_by_table to pass into BIM builder
    extra_measures_by_table = {}
    for tname, mlist in measures_refs.items():
        # If there are measures discovered, ensure table exists
        if tname not in tables:
            tables[tname] = []
        extra_measures_by_table[tname] = mlist

    print(f"[INFO] Tables         : {list(tables.keys())}")
    print(f"[INFO] Visualizations : {len(data.get('visualizations', []))}")
    print(f"[INFO] DAX measures   : {len(data.get('dax_measures', []))}")

    m_scripts    = {t: m_placeholder(t, cols) for t, cols in tables.items()}
    mashup_bytes = build_mashup_container(m_scripts)
    schema_json  = build_data_model_schema(data, tables, extra_measures_by_table)

    # Serialize outer layout JSON once and ensure valid JSON
    layout_json = json.dumps(layout_obj, ensure_ascii=False, separators=(",", ":"), indent=2)

    # Ensure UTF-16LE with BOM
    layout_bytes = b"\xff\xfe" + layout_json.encode("utf-16-le")

    # Optional safety check before writing: decode back and parse
    try:
        decoded = layout_bytes[2:].decode("utf-16-le")
        json.loads(decoded)
    except Exception as e:
        raise RuntimeError(f"Layout JSON validation failed after encoding: {e}")

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # FIX: Use corrected Content_Types and rels
        zf.writestr("[Content_Types].xml", build_content_types_xml().encode("utf-8"))
        zf.writestr("_rels/.rels",         RELS_XML.encode("utf-8"))
        zf.writestr("docProps/custom.xml", make_custom_xml(lb_name).encode("utf-8"))

        # Report
        zf.writestr("Report/Layout", layout_bytes)
        zf.writestr(
            "Report/StaticResources/SharedResources/BaseThemes/CY24SU10.json",
            THEME_JSON.encode("utf-8")
        )

        # Data layer
        zf.writestr("DataMashup",      mashup_bytes)

        # DataModelSchema required in PBI 2.130+
        zf.writestr("DataModelSchema", schema_json.encode("utf-8"))

        # Supporting files
        zf.writestr("Metadata",        make_metadata_json(lb_name).encode("utf-8"))
        zf.writestr("Settings",        SETTINGS_JSON.encode("utf-8"))

        # SecurityBindings must be a 4-byte zero (empty length prefix)
        zf.writestr("SecurityBindings", struct.pack("<I", 0))

        # Version is plain ASCII, no BOM
        zf.writestr("Version", "2.152.1279.0")

    # Quick verification inside the newly written PBIX
    with zipfile.ZipFile(out_path, "r") as zf:
        # verify SecurityBindings length
        sb = zf.read("SecurityBindings")
        assert len(sb) == 4 and sb == struct.pack("<I", 0)
        # verify DataMashup header
        dm = zf.read("DataMashup")
        if len(dm) >= 8:
            ver, length = struct.unpack("<II", dm[:8])
            if ver != 0:
                print("[WARN] DataMashup header version != 0 after write:", ver)

    size_kb = out_path.stat().st_size // 1024
    print(f"\n✅  PBIX written → {out_path.resolve()}  ({size_kb} KB)\n")

# -----------------------
# Validator (read-only) - prints diagnostics
# -----------------------
def pbix_deep_validate(pbix_path: Path):
    if not pbix_path.exists():
        print("ERROR: input file not found:", pbix_path)
        return 2

    with zipfile.ZipFile(pbix_path, 'r') as zf:
        print("Files in PBIX:")
        for n in zf.namelist():
            print("   ", n)
        print()

        def read(name):
            try:
                return zf.read(name)
            except KeyError:
                return None

        layout_b = read("Report/Layout")
        if layout_b is None:
            print("ERR Report/Layout missing")
        else:
            print("Report/Layout size:", len(layout_b))
            print("Report/Layout first 8 bytes hex:", layout_b[:8].hex())
            if layout_b.startswith(b'\xff\xfe'):
                print("OK Report/Layout has UTF-16LE BOM")
                txt = layout_b[2:].decode('utf-16-le', errors='replace')
            elif layout_b.startswith(b'\xef\xbb\xbf'):
                print("WARN Report/Layout has UTF-8 BOM (unexpected)")
                txt = layout_b.decode('utf-8-sig', errors='replace')
            else:
                print("WARN Report/Layout missing BOM; attempting utf-8 decode")
                txt = layout_b.decode('utf-8', errors='replace')

            try:
                outer = json.loads(txt)
                print("OK Outer JSON parsed")
            except Exception as e:
                print("ERR Outer JSON parse failed:", e)
                print("Preview:", txt[:400].replace("\n","\\n"))
                return 3

            # inner JSON strings
            vcs = []
            if outer.get('sections'):
                vcs = outer['sections'][0].get('visualContainers', [])
            for i, vc in enumerate(vcs):
                for key in ('config','query','dataTransforms'):
                    val = vc.get(key)
                    if val is None:
                        print(f"WARN visual {i} missing {key}")
                        continue
                    try:
                        json.loads(val)
                    except Exception as e:
                        print(f"ERR visual {i} inner JSON parse error {key}: {e}")
                        print("Preview:", val[:300].replace("\n","\\n"))
                        return 4
            print("OK inner JSON strings parse")

        ct_b = read("[Content_Types].xml")
        if ct_b is None:
            print("ERR [Content_Types].xml missing")
        else:
            ct = ct_b.decode('utf-8', errors='replace')
            print("[Content_Types].xml preview:")
            print(ct[:400].replace("\n","\\n"))
            if 'PartName="/' in ct or '/Report/Layout' in ct:
                print("ERR [Content_Types].xml contains leading slashes in Override PartName")
            else:
                print("OK [Content_Types].xml looks good")

        rels_b = read("_rels/.rels")
        if rels_b is None:
            print("ERR _rels/.rels missing")
        else:
            rels = rels_b.decode('utf-8', errors='replace')
            print("_rels/.rels preview:", rels[:400].replace("\n","\\n"))
            if "DataMashup" not in rels or "Report/Layout" not in rels:
                print("ERR _rels/.rels missing expected Relationship targets")
            else:
                print("OK _rels/.rels contains expected targets")

        dm_b = read("DataMashup")
        if dm_b is None:
            print("ERR DataMashup missing")
        else:
            print("DataMashup size:", len(dm_b))
            if len(dm_b) >= 8:
                ver, length = struct.unpack('<II', dm_b[:8])
                print("DataMashup header ver:", ver, "length:", length)
                if ver != 0:
                    print("ERR DataMashup version != 0 (Power BI expects 0)")
                    print("DataMashup first 64 bytes hex:", dm_b[:64].hex())
                else:
                    print("OK DataMashup version == 0")
            else:
                print("ERR DataMashup too small to contain header")

        sb_b = read("SecurityBindings")
        if sb_b is None:
            print("ERR SecurityBindings missing")
        else:
            print("SecurityBindings size:", len(sb_b), "hex:", sb_b[:16].hex())
            if len(sb_b) >= 4:
                val = struct.unpack('<I', sb_b[:4])[0]
                if val == 0:
                    print("OK SecurityBindings first uint == 0")
                else:
                    print("ERR SecurityBindings first uint != 0:", val)
            else:
                print("ERR SecurityBindings length < 4")

        v_b = read("Version")
        if v_b is None:
            print("WARN Version missing")
        else:
            print("Version bytes hex:", v_b[:16].hex())
            if v_b == b"3.0":
                print("OK Version is ASCII '3.0'")
            else:
                print("WARN Version not exactly '3.0' (may include BOM or other bytes)")

        dms_b = read("DataModelSchema")
        if dms_b is None:
            print("ERR DataModelSchema missing (required for PBI 2.130+)")
        else:
            try:
                dms = json.loads(dms_b.decode('utf-8', errors='replace'))
                tables = [t.get('name') for t in dms.get('model',{}).get('tables',[])]
                print("OK DataModelSchema parsed; tables:", tables)
            except Exception as e:
                print("ERR parsing DataModelSchema:", e)

    return 0

# -----------------------
# CLI entrypoint
# -----------------------
def main(argv):
    if len(argv) < 3:
        print("Usage: python ts_to_pbix_v3_fixed.py <csv_dir> <out.pbix>")
        print("Or:    python ts_to_pbix_v3_fixed.py validate <input.pbix>")
        sys.exit(2)

    if argv[1].lower() == "validate":
        pbix_path = Path(argv[2])
        return pbix_deep_validate(pbix_path)

    csv_dir = Path(argv[1])
    out_pbix = Path(argv[2])
    data = load_all(csv_dir)
    build_pbix(data, out_pbix)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
