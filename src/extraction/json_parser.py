import json
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)

class JsonParser:
    @staticmethod
    def execute(config: dict, api_response: Optional[str]) -> list[dict]:
        """
        Executes a JPERL LOCJSON/LOCJSONSEQ configuration on a JSON response payload.
        """
        if not api_response:
            logger.warning("JsonParser: api_response is empty or None")
            return []

        locjson = config.get("LOCJSON", "").strip()
        locjsonseq = config.get("LOCJSONSEQ", "").strip()
        if not locjson or not locjsonseq:
            logger.warning("JsonParser: LOCJSON or LOCJSONSEQ is missing in config")
            return []

        try:
            data = json.loads(api_response.strip())
        except Exception as e:
            logger.error("JsonParser: failed to parse API response as JSON: %s", e)
            return []

        try:
            # 1. Parse LOCJSON and LOCJSONSEQ
            semantics = [s.strip() for s in locjsonseq.split(",")]
            parts = [p.strip() for p in locjson.split("|XX|") if p.strip()]
            
            if not parts:
                return []

            field_paths = []
            array_path = ""

            if len(parts) >= 3:
                num_fields = len(semantics)
                field_paths.append(parts[0])
                for p in parts[1:num_fields]:
                    field_paths.append(p.split("|X|")[-1])
                
                # Extract array path from the second element prefix
                first_rel = parts[1]
                array_segs = first_rel.split("|X|")[:-1]
                array_path = ".".join(array_segs)
            elif len(parts) == 2:
                # Standard JPERL format: field1|X|field2|XX|array_path
                field_part, array_path = parts[0], parts[1]
                field_paths = [p.strip() for p in field_part.split("|X|")]
            else:
                # Fallback for simple single-field or basic path formats
                array_path = ""
                field_paths = [parts[0]]

            # 2. Extract Jobs Array
            jobs_array = JsonParser._get_json_value(data, array_path) if array_path else data
            if not isinstance(jobs_array, list):
                # If it's a dict containing the array, or a single dict
                if isinstance(jobs_array, dict):
                    # Try to find any list in the dict
                    lists = [v for v in jobs_array.values() if isinstance(v, list)]
                    if lists:
                        jobs_array = lists[0]
                    else:
                        jobs_array = [jobs_array]
                else:
                    jobs_array = []

            # 3. Extract Fields for each item
            job_objects = []
            for item in jobs_array:
                job = {}
                for i, field_rel_path in enumerate(field_paths):
                    if i < len(semantics):
                        val = JsonParser._get_json_value(item, field_rel_path)
                        job[semantics[i]] = str(val).strip() if val is not None else ""

                # Cross-map JOBID and JOBTITLE if missing
                if "JOBTITLE" not in job and "JOBID" in job:
                    job["JOBTITLE"] = job["JOBID"]
                elif "JOBTITLE" in job and "JOBID" not in job:
                    job["JOBID"] = job["JOBTITLE"]

                job_objects.append(job)

            return job_objects
        except Exception as e:
            logger.error("JsonParser: error executing JSON extraction: %s", e)
            return []

    @staticmethod
    def _get_json_value(data: Any, path_str: str) -> Any:
        """
        Traverses a nested dict/list using a dot-normalized or comma-separated path.
        """
        norm = path_str.replace(",", ".").replace("[", ".").replace("]", "").replace("|", ".")
        segments = [s.strip() for s in norm.split(".") if s.strip()]
        
        current = data
        for seg in segments:
            if isinstance(current, dict):
                if seg in current:
                    current = current[seg]
                else:
                    return None
            elif isinstance(current, list):
                try:
                    if seg.isdigit():
                        idx = int(seg)
                        if 0 <= idx < len(current):
                            current = current[idx]
                        else:
                            return None
                    else:
                        return None
                except Exception:
                    return None
            else:
                return None
        return current
