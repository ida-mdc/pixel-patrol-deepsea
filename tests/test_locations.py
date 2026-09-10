"""Where and when a recording was made.

The thing these tests are protecting is the distinction the module exists to keep:
a per-second vehicle track and one position for a whole dive are both a position
and they are not the same claim. A track answers only for the seconds it covers
and says nothing across a gap; a single fix answers for the whole recording. Both
have to survive being written to disk and read back in another process, because
that is how the pipeline gets hold of them.
"""

import json

import pytest

from pixel_patrol_deepsea.locations import (
    Fix, Track, dive_number, one_place, read_dive_summary, recorded_at,
    working_window, _kml_points, _path_polygon, _read_rov_track,
)

ROV_TRACK = "\n".join([
    "DATE,TIME,UNIXTIME,DEPTH,ALT,LAT_DD,LON_DD",
    # the surface rows have no fix at all, which is normal and not an error
    "10/27/2021,12:39:15.583000,1000.0,0.7,,,",
    "10/27/2021,12:40:15.583000,1060.0,-722.9,,31.212,-77.851",
    "10/27/2021,12:41:15.583000,1120.0,-842.4,0.1,31.210,-77.852",
])


def test_reads_the_clock_out_of_a_filename():
    noaa = "https://x/y/EX2107_VID_20211027T124027Z_ROVHD_Low.mp4"
    assert recorded_at(noaa).isoformat() == "2021-10-27T12:40:27+00:00"
    # OOI stopped writing the Z in 2018; the files are still UTC
    assert recorded_at("CAMHDA301-20240815T001500.mp4").isoformat() == "2024-08-15T00:15:00+00:00"
    assert recorded_at("CAMHDA301-20160815T000000Z.mov").hour == 0


def test_says_nothing_rather_than_guessing_when_there_is_no_clock():
    assert recorded_at("BD.mov") is None
    assert recorded_at("dive_02_part_3.mp4") is None


def test_finds_the_dive_a_recording_belongs_to():
    assert dive_number(".../Video/EX2107_DIVE01_20211027/Compressed/x.mp4") == 1
    assert dive_number(".../EX2503_DIVE13_20250426/y.mp4") == 13
    assert dive_number("CAMHDA301-20160815T030000Z.mp4") is None


def test_depth_is_read_as_a_positive_number_of_metres_down():
    track = _read_rov_track(ROV_TRACK, "test")
    # published as a negative elevation; everything downstream means depth
    assert track.at(1120.0).depth_m == pytest.approx(842.4)
    assert track.at(1120.0).altitude_m == pytest.approx(0.1)


def test_rows_with_no_position_are_dropped_rather_than_carried_as_zero():
    track = _read_rov_track(ROV_TRACK, "test")
    assert len(track) == 2
    assert all(fix.latitude for fix in track.fixes)


def test_a_track_answers_only_for_the_time_it_covers():
    track = _read_rov_track(ROV_TRACK, "test")
    assert track.at(1060.0).latitude == pytest.approx(31.212)
    # halfway between two fixes, the nearer one wins
    assert track.at(1100.0).latitude == pytest.approx(31.210)
    # far outside it there is no fix, because interpolating across a gap would put
    # a detection somewhere the vehicle never was
    assert track.at(9999.0) is None


def test_a_single_fix_holds_for_the_whole_recording():
    fixed = one_place(Fix(latitude=45.9, longitude=-130.0, depth_m=1542), "register")
    assert fixed.constant
    assert fixed.at(0.0) is fixed.at(1e9)
    assert fixed.at(1e9).depth_m == 1542


def test_a_single_fix_gets_a_footprint_so_the_map_will_show_it():
    # the map widget asks for latitude, longitude AND footprint, and shows nothing
    # unless all three are present
    fixed = one_place(Fix(latitude=45.9, longitude=-130.0), "register")
    geometry = json.loads(fixed.footprint)
    # A ring standing still, not a Point: the map reads coordinates[0] as an outer
    # ring, and a Point handed to that makes it iterate a number.
    assert geometry["type"] == "Polygon"
    assert geometry["coordinates"] == [[[-130.0, 45.9]] * 4]


def test_both_kinds_survive_being_written_and_read_back():
    for original in (_read_rov_track(ROV_TRACK, "test"),
                     one_place(Fix(latitude=1.5, longitude=2.5, depth_m=10), "register")):
        back = Track.from_csv(original.to_csv())
        assert len(back) == len(original)
        assert back.constant == original.constant
        assert back.footprint == original.footprint
        assert back.at(original.times[0]).latitude == original.fixes[0].latitude


def test_the_dive_outline_is_thinned_but_keeps_its_ends():
    points = [(float(i), float(i) * 2) for i in range(5000)]
    outline = json.loads(_path_polygon(points, most=100))
    ring = outline["coordinates"][0]
    assert outline["type"] == "Polygon"
    assert len(ring) <= 204                      # there and back
    assert ring[0] == [0.0, 0.0]
    assert ring[len(ring) // 2 - 1] == [4999.0, 9998.0]


def test_the_outline_is_a_ring_the_map_can_read_as_one():
    # Closed, four positions at the least, and enclosing no area - so the line
    # layer draws the path and the fill layer draws nothing.
    ring = json.loads(_path_polygon([(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)]))["coordinates"][0]
    assert ring[0] == ring[-1]
    assert len(ring) >= 4
    assert all(isinstance(position, list) and len(position) == 2 for position in ring)
    assert ring == [[0.0, 0.0], [1.0, 1.0], [2.0, 0.0],
                    [2.0, 0.0], [1.0, 1.0], [0.0, 0.0]]


def test_reads_coordinates_out_of_a_kml_line():
    kml = ("<kml><Placemark><LineString><coordinates>\n"
           "-77.853,31.209,0\n-77.854,31.210,0\n"
           "</coordinates></LineString></Placemark></kml>")
    assert _kml_points(kml) == [(-77.853, 31.209), (-77.854, 31.21)]


def test_the_working_window_is_the_bottom_time_only():
    start, stop = working_window({"on_bottom_at": "2021-10-27T13:42:08.983058",
                                  "off_bottom_at": "2021-10-27T19:52:25.150480"})
    assert start.hour == 13 and stop.hour == 19
    assert start.tzinfo is not None       # compared against UTC filenames


def test_no_window_when_the_report_does_not_say():
    assert working_window({}) is None
    assert working_window({"on_bottom_at": "not a time", "off_bottom_at": "either"}) is None


# ── the two generations of the dive report ────────────────────────────────────
#
# Both of these are the real thing, trimmed: the first is EX1903L2_DIVE01.txt as
# the 2019 cruise published it, the second EX2107_DIVE06.txt from 2021. The
# archive changed notation between them - "Max. depth" became "Max Vehicle Depth",
# degrees-and-minutes became decimal degrees - and reading only the newer one is
# not a cruise with no depths. It is a cruise whose descent gets analysed, because
# nothing knows when it reached the bottom.

OLD_REPORT = "\r\n".join([
    "\t  Dive Summary:\tEX1903L2_DIVE01",
    "^" * 52,
    "In Water:\t\t 2019-06-21T12:50:53.109671",
    "\t\t\t 28°, 15.064' N ; 79°, 36.072' W",
    "",
    "On Bottom:\t\t 2019-06-21T14:38:19.051810",
    "\t\t\t 28°, 15.148' N ; 79°, 35.923' W",
    "",
    "Off Bottom:\t\t 2019-06-21T20:05:00.878717",
    "\t\t\t 28°, 14.808' N ; 79°, 35.793' W",
    "",
    "Dive duration:\t\t 7:55:28",
    "",
    "Max. depth: \t\t 805.0 m",
    "",
])

NEW_REPORT = "\r\n".join([
    "      Dive Summary:  EX2107_DIVE06",
    "^" * 41,
    "Dive Type:  Normal",
    "",
    "In Water:   2021-11-03T12:44:56.946453",
    "            30.191408243835685 ; -76.16497038526963",
    "",
    "On Bottom:  2021-11-03T15:21:39.434243",
    "            30.19140840883748 ; -76.16348021710911",
    "",
    "Off Bottom: 2021-11-03T18:27:00.230753",
    "            30.19048589432155 ; -76.16497938083789",
    "",
    "Max Vehicle Depth:     3650.3 m",
    "",
    "Min Seafloor Depth:    3594.3 m",
    "",
])


def test_reads_the_depth_out_of_either_generation_of_report():
    assert read_dive_summary(OLD_REPORT)["max_depth_m"] == pytest.approx(805.0)
    assert read_dive_summary(NEW_REPORT)["max_depth_m"] == pytest.approx(3650.3)


def test_degrees_and_minutes_are_the_same_position_as_decimal_degrees():
    old = read_dive_summary(OLD_REPORT)
    # 28 deg 15.148' N, 79 deg 35.923' W, west of Greenwich and so negative
    assert old["latitude"] == pytest.approx(28 + 15.148 / 60)
    assert old["longitude"] == pytest.approx(-(79 + 35.923 / 60))
    assert read_dive_summary(NEW_REPORT)["latitude"] == pytest.approx(30.19140840883748)


def test_the_bottom_time_is_read_from_the_older_report_too():
    # The reason the notation matters at all: without this window every hour of
    # descent through open water is analysed like the seafloor.
    start, stop = working_window(read_dive_summary(OLD_REPORT))
    assert start.isoformat() == "2019-06-21T14:38:19.051810+00:00"
    assert stop.isoformat() == "2019-06-21T20:05:00.878717+00:00"


def test_a_moment_with_no_position_keeps_its_time():
    # EX1605L1 dive 12: the navigation was not recorded when the vehicle entered
    # the water. That is one missing coordinate, not a missing dive.
    report = OLD_REPORT.replace("28°, 15.064' N ; 79°, 36.072' W", "N/A ; N/A")
    summary = read_dive_summary(report)
    assert summary["in_water_at"].startswith("2019-06-21T12:50:53")
    assert "in_water_latitude" not in summary
    assert summary["on_bottom_latitude"] == pytest.approx(28 + 15.148 / 60)


# The 2016 cruises head their track differently and put depth after longitude
# rather than before it. Reading the header rather than the column order is what
# makes both files the same thing to everything downstream.
OLD_ROV_TRACK = "\n".join([
    "date(mm/dd/yyyy),time(HH:MM:SS.SSS),time (unix sec),lat (dec. deg.),"
    " lon (dec. deg.), depth (m), alt (m)  ",
    "04/20/2016,20:47:00.000,1461185220.000,12.860403590961,144.301759555744,  49.6,  nan ",
    "04/20/2016,21:47:00.000,1461188820.000,12.860516666666,144.304116666666,  634.2,  3.1 ",
])


def test_a_track_is_read_by_its_header_not_its_column_order():
    track = _read_rov_track(OLD_ROV_TRACK, "test")
    assert len(track) == 2
    fix = track.at(1461188820.0)
    assert fix.latitude == pytest.approx(12.8605166)
    assert fix.longitude == pytest.approx(144.3041166)
    # published positive here and negative on the newer cruises; depth either way
    assert fix.depth_m == pytest.approx(634.2)
    assert fix.altitude_m == pytest.approx(3.1)


def test_a_track_with_no_position_columns_is_no_track():
    assert _read_rov_track("a,b\n1,2\n", "test") is None
