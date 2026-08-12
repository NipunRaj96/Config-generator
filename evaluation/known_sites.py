"""
evaluation/known_sites.py
─────────────────────────
Dataset of known sites and their expected JPERL mappings for offline evaluation.
"""

TEST_SITES = [
    {
        "crawler_id": "greenhouse_1",
        "company_name": "Greenhouse Test Site",
        "site_id": "greenhouse_UC",
        "career_site_url": "https://careers.greenhouse.io/acme",
        "expected": {
            "api_url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
            "jobs_path": "jobs",
            "pagination_type": "none",
            "fields": {
                "JOBTITLE": "title",
                "JOBID": "id",
                "LOCATION": "location.name"
            }
        },
        "candidates": [
            {
                "url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
                "method": "GET",
                "response_body": '{"jobs": [{"id": 123, "title": "Software Engineer", "location": {"name": "SF"}}]}'
            },
            {
                "url": "https://api.mixpanel.com/track",
                "method": "POST",
                "response_body": '{"status": 1}'
            }
        ]
    },
    {
        "crawler_id": "lever_1",
        "company_name": "Lever Test Site",
        "site_id": "lever_UC",
        "career_site_url": "https://jobs.lever.co/acme",
        "expected": {
            "api_url": "https://api.lever.co/v0/postings/acme?mode=json",
            "jobs_path": "data",
            "pagination_type": "none",
            "fields": {
                "JOBTITLE": "text",
                "JOBID": "id",
                "LOCATION": "categories.location"
            }
        },
        "candidates": [
            {
                "url": "https://api.lever.co/v0/postings/acme?mode=json",
                "method": "GET",
                "response_body": '[{"id": "abc", "text": "Product Manager", "categories": {"location": "Remote"}}]'
            }
        ]
    },
    {
        "crawler_id": "workday_1",
        "company_name": "Workday Test Site",
        "site_id": "workday_UC",
        "career_site_url": "https://acme.myworkdayjobs.com/careers",
        "expected": {
            "api_url": "https://acme.myworkdayjobs.com/wday/cxs/acme/careers/jobs",
            "jobs_path": "jobPostings",
            "pagination_type": "offset",
            "fields": {
                "JOBTITLE": "title",
                "JOBID": "bulletins.id",
                "LOCATION": "locationsText"
            }
        },
        "candidates": [
            {
                "url": "https://acme.myworkdayjobs.com/wday/cxs/acme/careers/jobs",
                "method": "POST",
                "request_body": '{"limit": 20, "offset": 0}',
                "response_body": '{"jobPostings": [{"title": "Data Scientist", "bulletins": {"id": "req-99"}, "locationsText": "London"}]}'
            }
        ]
    },
    {
        "crawler_id": "custom_json_1",
        "company_name": "Inspire Infosol",
        "site_id": "inspire_UC",
        "career_site_url": "https://www.inspireinfosol.com/careers",
        "expected": {
            "api_url": "https://www.inspireinfosol.com/assets/data/careers.json",
            "jobs_path": "openRoles",
            "pagination_type": "none",
            "fields": {
                "JOBTITLE": "title",
                "JOBID": "id"
            }
        },
        "candidates": [
            {
                "url": "https://www.inspireinfosol.com/assets/data/careers.json",
                "method": "GET",
                "response_body": '{"openRoles": [{"id": "1", "title": "Cloud SRE", "location": "Noida"}]}'
            }
        ]
    },
    {
        "crawler_id": "srp_custom_1",
        "company_name": "Custom SRP HTML Site",
        "site_id": "srp_custom_UC",
        "career_site_url": "https://srpcorp.com/careers",
        "page_html": '<div class="job-list"><div class="job-item"><h3>Software Engineer</h3></div><div class="job-item"><h3>QA Analyst</h3></div></div>',
        "expected": {
            "xpath": "//div[@class='job-list']//div[contains(@class, 'job-item')]",
            "matches": 2
        }
    },
    {
        "crawler_id": "regex_custom_1",
        "company_name": "Custom Regex Site",
        "site_id": "regex_custom_UC",
        "career_site_url": "https://regexcorp.com/careers",
        "page_html": '<div class="job"><h2>Frontend Engineer</h2></div><div class="job"><h2>Backend Engineer</h2></div>',
        "expected": {
            "locrgx": "(?s)<div class=\"job\"><h2>([^<]+)</h2></div>",
            "locrgxseq": "JOBTITLE",
            "matches": 2
        }
    },
    {
        "crawler_id": "designersio_known",
        "company_name": "Designers.io Dropdown Site",
        "site_id": "designersio_UC",
        "career_site_url": "https://proffus.com/careers/",
        "page_html": '<select name="form_fields[field_e54a2f0]"><option value="1">Social Media Designer</option><option value="2">Shopify Developer</option></select>',
        "expected": {
            "locrgx": "(?s)<option.+?>(([^<]+))",
            "locrgxseq": "JOBID,JOBTITLE",
            "matches": 2
        }
    }
]
