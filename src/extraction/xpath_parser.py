import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class XPathParser:
    @staticmethod
    def execute(config: dict, page_html: Optional[str]) -> list[dict]:
        """
        Executes an XPath expression on HTML and extracts unified job objects.
        """
        if not page_html:
            logger.warning("XPathParser: page_html is empty or None")
            return []

        xpath = config.get("xpath", "").strip()
        if not xpath:
            logger.warning("XPathParser: xpath is missing in config")
            return []

        try:
            from lxml import html
            tree = html.fromstring(page_html)
            elements = tree.xpath(xpath)
        except ImportError:
            # Fallback to built-in xml.etree.ElementTree for testing environments without lxml
            import xml.etree.ElementTree as ET
            try:
                wrapped = f"<root>{page_html}</root>"
                tree = ET.fromstring(wrapped)
                
                # Parse segments to look at the last target segment
                segments = [s.strip() for s in xpath.split("/") if s.strip()]
                target_seg = segments[-1] if segments else xpath
                
                tag_match = re.search(r'^([a-zA-Z0-9_-]+)', target_seg)
                tag_name = tag_match.group(1) if tag_match else "*"
                
                class_match = re.search(r"(?:@class=['\"]([^'\"]+)['\"]|contains\(@class,\s*['\"]([^'\"]+)['\"]\))", target_seg)
                target_class = None
                if class_match:
                    target_class = class_match.group(1) or class_match.group(2)
                
                elements = []
                for el in tree.iter(tag_name):
                    if target_class:
                        el_class = el.get("class") or ""
                        if target_class in el_class:
                            elements.append(el)
                    else:
                        elements.append(el)
            except Exception as e:
                logger.error("XPathParser: ET fallback failed: %s", e)
                return []

        try:
            job_objects = []
            for el in elements:
                text = ""
                if hasattr(el, 'text_content'):
                    text = el.text_content().strip()
                elif hasattr(el, 'text'):
                    # ET element text fallback (including child text recursively)
                    parts = [el.text or ""]
                    for child in el:
                        parts.append(child.text or "")
                        if child.tail:
                            parts.append(child.tail)
                    text = "".join(parts).strip()
                elif isinstance(el, str):
                    text = el.strip()

                text = re.sub(r'\s+', ' ', text).strip()
                job_objects.append({
                    "JOBTITLE": text,
                    "JOBID": text
                })

            return job_objects
        except Exception as e:
            logger.error("XPathParser: failed to execute xpath extraction: %s", e)
            return []

