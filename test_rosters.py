#!/usr/bin/env python3
"""Quick test of filename extraction logic"""

from rosters import SECTION_105, SECTION_106

# Test a few filenames
test_files = [
    ('aguirregutierrezlupe_5727063_94081474.pdf', 105),
    ('vargaszaragozadolfie_5659439_94074075.pdf', 105),
    ('newmanmia_LATE_5663657_94089657.pdf', 105),
    ('ajpopmejiaabner_5722560_94069670.pdf', 106),
    ('hernandezblandonceleste_5717221_94081856.pdf', 106),
    ('kimmatthew_5773429_94071516.pdf', 106),
]

print('Testing filename matching...\n')
print(f'{"Filename":50} {"Extracted":30} {"Expected":10} {"Match":10}')
print('-' * 100)

for filename, expected_section in test_files:
    base = filename.lower().replace('.pdf', '').strip()
    first_part = base.split('_')[0]
    
    match_105 = any(name in first_part for name in SECTION_105)
    match_106 = any(name in first_part for name in SECTION_106)
    
    if match_105 and not match_106:
        detected = 105
    elif match_106 and not match_105:
        detected = 106
    elif match_105 and match_106:
        detected = "CONFLICT"
    else:
        detected = "NO MATCH"
    
    match_str = "✓" if detected == expected_section else "✗"
    print(f'{filename:50} {first_part:30} {expected_section:10} {str(detected):10} {match_str}')
