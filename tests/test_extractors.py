from app.extractors import extract_candidate_info, extract_position_from_filename


def test_extract_position_from_bracket_name():
    file_name = "【岗位甲工程师(方向A)_北京 40-70K】候选人甲 26年应届生.pdf"
    assert extract_position_from_filename(file_name) == "岗位甲工程师(方向A)"


def test_extract_candidate_info_basic():
    text = """
    姓名：张三
    手机：13800138000
    邮箱：zhangsan@example.com
    本科，3年工作经验
    熟悉 Python、FastAPI、MySQL
    """
    info = extract_candidate_info(text, "【岗位乙工程师_北京】张三.pdf")
    assert info.name == "张三"
    assert info.phone == "13800138000"
    assert info.email == "zhangsan@example.com"
    assert info.education == "本科"
    assert info.years_experience == "3年"
    assert "Python" in (info.skills or "")


def test_extract_position_maimai_pattern_1():
    file_name = "岗位丙工程师（2026校招）-候选人甲【脉脉招聘】.pdf"
    assert extract_position_from_filename(file_name) == "岗位丙工程师（2026校招）"


def test_extract_position_maimai_pattern_2():
    file_name = "【岗位丙工程师（2026校招）_北京 20-40K】候选人乙 26年应届生.pdf"
    assert extract_position_from_filename(file_name) == "岗位丙工程师（2026校招）"


def test_extract_position_maimai_pattern_3():
    file_name = "【岗位丁实习生_北京 300-500元_天】候选人丙 27年应届生.pdf"
    assert extract_position_from_filename(file_name) == "岗位丁实习生"


def test_extract_position_maimai_pattern_4():
    file_name = "【岗位戊工程（方向B）_北京 40-70K】候选人丁 4年.pdf"
    assert extract_position_from_filename(file_name) == "岗位戊工程（方向B）"


def test_extract_name_from_filename_patterns():
    info1 = extract_candidate_info("", "岗位丙工程师（2026校招）-候选人甲【脉脉招聘】.pdf")
    info2 = extract_candidate_info("", "【岗位戊工程（方向B）_北京 40-70K】候选人丁 4年.pdf")
    assert info1.name == "候选人甲"
    assert info2.name == "候选人丁"


def test_extract_position_fallback_patterns():
    assert extract_position_from_filename("示例候选人A-岗位己研发工程师.pdf") == "岗位己研发工程师"
    assert extract_position_from_filename("示例候选人B-岗位庚开发工程师.pdf") == "岗位庚开发工程师"
    assert extract_position_from_filename("示例候选人C-岗位辛数据开发-示例本科-8年.pdf") == "岗位辛数据开发"
    assert extract_position_from_filename("示例候选人D-岗位壬软件工程师.pdf") == "岗位壬软件工程师"
