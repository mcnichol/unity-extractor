PoliceDepartment binary-to-Godot converter
=========================================
Requires Python 3.9 or newer. No pip packages, Unreal Editor, Blender or Flatpak.
Keep all three .py files together.

Run from the extracted tool directory:

python3 police_godot_export.py /path/to/PoliceHeb64e6c045522V1/data/Content --output /path/to/police_godot

Output must be a NEW directory. The source assets are read only.
Supply Content, not the directory above data and not a .unitypackage.
If using a Git checkout, run git lfs pull first to fetch the binary asset data.

Outputs:
 assets/police_department/models/     GLB geometry with UVs and normals
 assets/police_department/textures/   full-size source PNG textures
 assets/police_department/materials/  Godot shader/material resources
 assets/police_department/scenes/     Godot TSCN objects with materials assigned
 project.godot                       standalone Godot 4 project
 export_report.json                  per-file results and material limitations
 README.txt                          import instructions

Open project.godot or copy assets/police_department into your existing project's
root assets directory. Use the TSCN scenes for textured objects; bare GLB models
contain geometry without the external Godot material assignments.

Tested asset set: mcnichol/unity-extractor, PoliceHeb64e6c045522V1.
Actual export: 214 meshes, 160 textures, 50 translated PBR materials plus fallback.
Godot 4.5 load validation: 214 scenes, 300 surfaces, no missing material overrides.
The conversion logic used to create those files is supplied here.

Important scope:
This is a format-specific converter for the tested UE4 editor package layout,
not a universal .uasset converter. It targets legacy -7, versioned UE4 packages
with compressed UE4 MeshDescription and embedded PNG texture source data.
No native geometry substitutions, resizing, or thumbnail substitution.
Cooked assets, UE5 layouts, split bulk files and other compression/layouts may
be unsupported; see the report. No Blueprint, map, animation or collision import.
Generated reduction LODs are not rebuilt. Default output is first imported mesh.
--all-imported-lods exports additional recoverable imported source descriptions;
it does not generate missing LODs or configure switching distances in Godot.

Materials use an approximate PBR shader, not the complete Unreal shader graph.
Custom glass, alpha decals, parallax, masks and other effects need manual review.
Texture channels are configured for this pack's RMA convention.
Source normals are adapted for Unreal's normal-map import convention.

File roles:
 police_godot_export.py     conversion, bulk decompression, GLB and Godot output
 read_uasset.py            package tables and tagged-property deserialization
 ue4_mesh_description.py   topology and vertex attribute decoding
 THIRD_PARTY_LICENSE.txt   MIT attribution for adapted mesh-description logic

Exit codes: 0 successful, 2 one or more assets failed, 1 top-level error.
Inspect export_report.json even when conversion succeeds for material caveats.
