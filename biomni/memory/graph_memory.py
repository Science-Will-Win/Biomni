import os
import json
from hashlib import sha256
from neo4j import GraphDatabase

class GraphMemory:
    def __init__(self):
        # 도커 환경변수 우선 적용
        self.user = os.getenv("NEO4J_USER", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD", "biomnipassword")
        configured_uri = os.getenv("NEO4J_URI")
        candidates = [
            configured_uri,
            "bolt://neo4j:7687",
            "bolt://biomni-neo4j:7687",
            "bolt://host.docker.internal:7687",
            "bolt://172.17.0.1:7687",
        ]
        seen = set()
        self.driver = None
        last_error = None
        for uri in [u for u in candidates if u and not (u in seen or seen.add(u))]:
            try:
                driver = GraphDatabase.driver(uri, auth=(self.user, self.password))
                driver.verify_connectivity()
                self.uri = uri
                self.driver = driver
                print(f"[EXPEL][NEO4J] connected uri={uri}")
                break
            except Exception as e:
                last_error = e
                try:
                    driver.close()
                except Exception:
                    pass
        if self.driver is None:
            raise last_error or RuntimeError("Neo4j connection failed")

    def close(self):
        self.driver.close()

    def reset_all(self):
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def save_error_and_reflection(self, task_desc, tool_name, error_msg, reflection):
        query = """
        MERGE (t:Task {name: $task})
        MERGE (tl:Tool {name: $tool})
        MERGE (e:Error {message: $error})
        MERGE (i:Insight {content: $reflection, processed: false})
        MERGE (t)-[:USED]->(tl) MERGE (tl)-[:RAISED]->(e) MERGE (e)-[:RESOLVED_BY]->(i)
        """
        with self.driver.session() as session:
            session.run(query, task=task_desc, tool=tool_name, error=error_msg, reflection=reflection)

    def fetch_global_insights(self, tool_name):
        query = """
        MATCH (tl:Tool {name: $tool})-[:HAS_GLOBAL_INSIGHT|HAS_INSIGHT]->(gi)
        WHERE gi:GlobalInsight OR gi:Insight
        WITH DISTINCT gi
        ORDER BY coalesce(gi.upvotes, 0) DESC, gi.updated_at DESC
        RETURN gi.content AS content
        LIMIT 20
        """
        with self.driver.session() as session:
            return [{"content": r["content"]} for r in session.run(query, tool=tool_name)]

    def upsert_insight(self, task_name, tool_name, insight, metadata=None):
        metadata = metadata or {}
        metadata_json = json.dumps(metadata, ensure_ascii=False, default=str)
        insight_id = sha256(f"{tool_name}|{insight}".encode("utf-8")).hexdigest()
        query = """
        MERGE (t:Task {name: $task})
        MERGE (tl:Tool {name: $tool})
        MERGE (i:Insight {id: $insight_id})
        SET i.content = $insight,
            i.metadata_json = $metadata_json,
            i.status = coalesce(i.status, 'active'),
            i.operation = $operation,
            i.insight_type = $insight_type,
            i.confidence = CASE
                WHEN i.confidence IS NULL AND $confidence > 1.0 THEN 1.0
                WHEN i.confidence IS NULL AND $confidence < 0.0 THEN 0.0
                WHEN i.confidence IS NULL THEN $confidence
                WHEN ((i.confidence + $confidence) / 2.0) > 1.0 THEN 1.0
                WHEN ((i.confidence + $confidence) / 2.0) < 0.0 THEN 0.0
                ELSE (i.confidence + $confidence) / 2.0
            END,
            i.source_success = $source_success,
            i.task_name = $task,
            i.tool_name = $tool,
            i.updated_at = datetime(),
            i.upvotes = coalesce(i.upvotes, 0),
            i.downvotes = coalesce(i.downvotes, 0)
        MERGE (t)-[:PRODUCED_INSIGHT]->(i)
        MERGE (tl)-[:HAS_INSIGHT]->(i)
        """
        with self.driver.session() as session:
            session.run(
                query,
                task=task_name,
                tool=tool_name,
                insight=insight,
                insight_id=insight_id,
                metadata_json=metadata_json,
                insight_type=metadata.get("insight_type", "best_practice"),
                operation=metadata.get("operation", "ADD"),
                confidence=float(metadata.get("confidence", 0.5) or 0.5),
                source_success=bool(metadata.get("success", False)),
            )

    def fetch_expel_context(self, task_name, tool_name, limit=5):
        insight_query = """
        MATCH (tl:Tool {name: $tool})-[:HAS_INSIGHT]->(i:Insight)
        WHERE coalesce(i.status, 'active') = 'active'
          AND coalesce(i.insight_type, 'best_practice') <> 'failure_pattern'
          AND coalesce(i.confidence, 0.5) >= 0.6
        RETURN i.content AS content,
               i.insight_type AS insight_type,
               coalesce(i.confidence, 0.5) AS confidence,
               coalesce(i.upvotes, 0) AS upvotes,
               coalesce(i.downvotes, 0) AS downvotes,
               i.updated_at AS updated_at
        ORDER BY confidence DESC, upvotes DESC, updated_at DESC
        LIMIT $limit
        """
        failure_query = """
        MATCH (tl:Tool {name: $tool})-[:HAS_INSIGHT]->(i:Insight)
        WHERE coalesce(i.status, 'active') = 'active'
          AND coalesce(i.insight_type, '') = 'failure_pattern'
        RETURN i.content AS content,
               coalesce(i.confidence, 0.5) AS confidence,
               i.updated_at AS updated_at
        ORDER BY confidence DESC, updated_at DESC
        LIMIT $limit
        """
        trajectory_query = """
        MATCH (t:Task)-[:HAS_RUN]->(r:Run)-[:PRODUCED_EXPERIENCE]->(e:Experience)
        WHERE r.success = true
          AND (
            t.name = $task
            OR toLower(t.name) CONTAINS toLower($task)
            OR toLower($task) CONTAINS toLower(t.name)
          )
        RETURN e.trajectory AS trajectory,
               e.outcome AS outcome,
               e.score AS score,
               r.trace_id AS trace_id,
               r.conv_id AS conv_id,
               e.updated_at AS updated_at
        ORDER BY e.score DESC, e.updated_at DESC
        LIMIT $limit
        """
        resource_query = """
        MATCH (res)
        WHERE (res:Tool OR res:DataResource OR res:Library)
          AND coalesce(res.confidence, 0.5) >= 0.6
        RETURN labels(res) AS labels,
               res.name AS name,
               res.description AS description,
               coalesce(res.confidence, 0.5) AS confidence,
               coalesce(res.success_count, 0) AS success_count,
               coalesce(res.failure_count, 0) AS failure_count
        ORDER BY confidence DESC, success_count DESC
        LIMIT $limit
        """
        resource_avoid_query = """
        MATCH (res)
        WHERE (res:Tool OR res:DataResource OR res:Library)
          AND coalesce(res.failure_count, 0) > coalesce(res.success_count, 0)
        RETURN labels(res) AS labels,
               res.name AS name,
               res.description AS description,
               coalesce(res.confidence, 0.5) AS confidence,
               coalesce(res.success_count, 0) AS success_count,
               coalesce(res.failure_count, 0) AS failure_count
        ORDER BY failure_count DESC, confidence ASC
        LIMIT $limit
        """
        with self.driver.session() as session:
            insights = [
                {
                    "content": r["content"],
                    "insight_type": r["insight_type"],
                    "confidence": r["confidence"],
                    "upvotes": r["upvotes"],
                    "downvotes": r["downvotes"],
                }
                for r in session.run(insight_query, tool=tool_name, limit=limit)
                if r["content"]
            ]
            failure_patterns = [
                {
                    "content": r["content"],
                    "confidence": r["confidence"],
                }
                for r in session.run(failure_query, tool=tool_name, limit=limit)
                if r["content"]
            ]
            trajectories = [
                {
                    "trajectory": r["trajectory"],
                    "outcome": r["outcome"],
                    "score": r["score"],
                    "trace_id": r["trace_id"],
                    "conv_id": r["conv_id"],
                }
                for r in session.run(trajectory_query, task=task_name or "", limit=limit)
                if r["trajectory"]
            ]
            resource_recommendations = [
                {
                    "labels": r["labels"],
                    "name": r["name"],
                    "description": r["description"],
                    "confidence": r["confidence"],
                    "success_count": r["success_count"],
                    "failure_count": r["failure_count"],
                }
                for r in session.run(resource_query, limit=limit)
                if r["name"]
            ]
            resource_avoidances = [
                {
                    "labels": r["labels"],
                    "name": r["name"],
                    "description": r["description"],
                    "confidence": r["confidence"],
                    "success_count": r["success_count"],
                    "failure_count": r["failure_count"],
                }
                for r in session.run(resource_avoid_query, limit=limit)
                if r["name"]
            ]
        return {
            "insights": insights,
            "failure_patterns": failure_patterns,
            "trajectories": trajectories,
            "resource_recommendations": resource_recommendations,
            "resource_avoidances": resource_avoidances,
        }

    def save_resource_selection(
        self,
        task_name,
        run_id,
        trace_id,
        tools=None,
        data_resources=None,
        libraries=None,
    ):
        tools = tools or []
        data_resources = data_resources or []
        libraries = libraries or []

        def _name(item):
            if isinstance(item, dict):
                return item.get("name") or item.get("id") or ""
            return str(item or "")

        def _desc(item):
            return item.get("description", "") if isinstance(item, dict) else ""

        def _module(item):
            return item.get("module", "") if isinstance(item, dict) else ""

        with self.driver.session() as session:
            session.run(
                """
                MERGE (t:Task {name: $task})
                MERGE (r:Run {conv_id: $run_id})
                SET r.trace_id = $trace_id,
                    r.updated_at = datetime()
                MERGE (t)-[:HAS_RUN]->(r)
                """,
                task=task_name,
                run_id=run_id,
                trace_id=trace_id,
            )
            for item in tools:
                name = _name(item)
                if not name:
                    continue
                session.run(
                    """
                    MATCH (r:Run {conv_id: $run_id})
                    MERGE (res:Tool {name: $name})
                    SET res.description = coalesce(res.description, $description),
                        res.module = coalesce(res.module, $module),
                        res.selected_count = coalesce(res.selected_count, 0) + 1,
                        res.confidence = coalesce(res.confidence, 0.5),
                        res.updated_at = datetime()
                    MERGE (r)-[:SELECTED_TOOL]->(res)
                    """,
                    run_id=run_id,
                    name=name,
                    description=_desc(item),
                    module=_module(item),
                )
            for item in data_resources:
                name = _name(item)
                if not name:
                    continue
                session.run(
                    """
                    MATCH (r:Run {conv_id: $run_id})
                    MERGE (res:DataResource {name: $name})
                    SET res.description = coalesce(res.description, $description),
                        res.selected_count = coalesce(res.selected_count, 0) + 1,
                        res.confidence = coalesce(res.confidence, 0.5),
                        res.updated_at = datetime()
                    MERGE (r)-[:SELECTED_DATA]->(res)
                    """,
                    run_id=run_id,
                    name=name,
                    description=_desc(item),
                )
            for item in libraries:
                name = _name(item)
                if not name:
                    continue
                session.run(
                    """
                    MATCH (r:Run {conv_id: $run_id})
                    MERGE (res:Library {name: $name})
                    SET res.description = coalesce(res.description, $description),
                        res.selected_count = coalesce(res.selected_count, 0) + 1,
                        res.confidence = coalesce(res.confidence, 0.5),
                        res.updated_at = datetime()
                    MERGE (r)-[:SELECTED_LIBRARY]->(res)
                    """,
                    run_id=run_id,
                    name=name,
                    description=_desc(item),
                )

    def save_expel_experience(
        self,
        task_name,
        run_id,
        step_index,
        step_name,
        tool_name,
        success,
        final_result,
        full_response,
        system_prompt,
        trace_id=None,
        metadata=None,
    ):
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, default=str)
        result_json = json.dumps(final_result or {}, ensure_ascii=False, default=str)
        trajectory = (
            f"Task: {task_name}\n"
            f"Step {step_index}: {step_name}\n"
            f"Tool: {tool_name}\n"
            f"Success: {success}\n"
            f"Result: {result_json[:3000]}\n"
            f"LLM Response: {(full_response or '')[:3000]}"
        )
        exp_id = sha256(
            f"{run_id}|{step_index}|{tool_name}|{result_json[:500]}".encode("utf-8")
        ).hexdigest()
        llm_id = sha256(f"{run_id}|{step_index}|llm".encode("utf-8")).hexdigest()
        obs_id = sha256(f"{run_id}|{step_index}|obs".encode("utf-8")).hexdigest()
        query = """
        MERGE (t:Task {name: $task})
        MERGE (r:Run {conv_id: $run_id})
        SET r.trace_id = $trace_id,
            r.success = coalesce(r.success, false) OR $success,
            r.updated_at = datetime()
        MERGE (t)-[:HAS_RUN]->(r)
        MERGE (s:Step {run_id: $run_id, index: $step_index})
        SET s.name = $step_name,
            s.success = $success,
            s.updated_at = datetime()
        MERGE (r)-[:HAS_STEP]->(s)
        MERGE (tl:Tool {name: $tool})
        MERGE (s)-[:USED_TOOL]->(tl)
        MERGE (lc:LLMCall {id: $llm_id})
        SET lc.system_prompt = $system_prompt,
            lc.answer = $full_response,
            lc.phase = 'step',
            lc.updated_at = datetime()
        MERGE (s)-[:HAS_LLM_CALL]->(lc)
        MERGE (o:Observation {id: $obs_id})
        SET o.result_json = $result_json,
            o.success = $success,
            o.updated_at = datetime()
        MERGE (s)-[:PRODUCED_OBSERVATION]->(o)
        MERGE (e:Experience {id: $exp_id})
        SET e.trajectory = $trajectory,
            e.outcome = CASE WHEN $success THEN 'success' ELSE 'failure' END,
            e.score = CASE WHEN $success THEN 1.0 ELSE 0.0 END,
            e.metadata_json = $metadata_json,
            e.updated_at = datetime()
        MERGE (r)-[:PRODUCED_EXPERIENCE]->(e)
        MERGE (e)-[:USED_TOOL]->(tl)
        """
        with self.driver.session() as session:
            session.run(
                query,
                task=task_name,
                run_id=run_id,
                step_index=step_index,
                step_name=step_name,
                tool=tool_name,
                success=bool(success),
                result_json=result_json,
                full_response=full_response or "",
                system_prompt=system_prompt or "",
                trajectory=trajectory,
                exp_id=exp_id,
                llm_id=llm_id,
                obs_id=obs_id,
                trace_id=trace_id,
                metadata_json=metadata_json,
            )
            session.run(
                """
                MATCH (r:Run {conv_id: $run_id})-[:SELECTED_TOOL]->(tool:Tool)
                MATCH (s:Step {run_id: $run_id, index: $step_index})
                MATCH (e:Experience {id: $exp_id})
                MERGE (s)-[:USED_TOOL]->(tool)
                MERGE (e)-[:USED_RESOURCE]->(tool)
                """,
                run_id=run_id,
                step_index=step_index,
                exp_id=exp_id,
            )
            session.run(
                """
                MATCH (r:Run {conv_id: $run_id})-[:SELECTED_DATA]->(data:DataResource)
                MATCH (s:Step {run_id: $run_id, index: $step_index})
                MATCH (e:Experience {id: $exp_id})
                MERGE (s)-[:USED_DATA]->(data)
                MERGE (e)-[:USED_RESOURCE]->(data)
                """,
                run_id=run_id,
                step_index=step_index,
                exp_id=exp_id,
            )
            session.run(
                """
                MATCH (r:Run {conv_id: $run_id})-[:SELECTED_LIBRARY]->(lib:Library)
                MATCH (s:Step {run_id: $run_id, index: $step_index})
                MATCH (e:Experience {id: $exp_id})
                MERGE (s)-[:USED_LIBRARY]->(lib)
                MERGE (e)-[:USED_RESOURCE]->(lib)
                """,
                run_id=run_id,
                step_index=step_index,
                exp_id=exp_id,
            )

    def save_ragas_evaluation(self, task_name, run_id, trace_id, scores, contexts=None):
        scores = scores or {}
        contexts_json = json.dumps(contexts or [], ensure_ascii=False, default=str)
        eval_id = sha256(f"{run_id}|ragas|{trace_id}".encode("utf-8")).hexdigest()
        query = """
        MERGE (t:Task {name: $task})
        MERGE (r:Run {conv_id: $run_id})
        SET r.trace_id = coalesce(r.trace_id, $trace_id),
            r.updated_at = datetime()
        MERGE (t)-[:HAS_RUN]->(r)
        MERGE (ev:RagasEvaluation {id: $eval_id})
        SET ev.faithfulness = $faithfulness,
            ev.answer_relevance = $answer_relevance,
            ev.context_relevance = $context_relevance,
            ev.skipped = $skipped,
            ev.skip_reason = $skip_reason,
            ev.error = $error,
            ev.contexts_json = $contexts_json,
            ev.updated_at = datetime()
        MERGE (ev)-[:EVALUATES]->(r)
        """
        with self.driver.session() as session:
            session.run(
                query,
                task=task_name,
                run_id=run_id,
                trace_id=trace_id,
                eval_id=eval_id,
                faithfulness=float(scores.get("faithfulness", 0.0) or 0.0),
                answer_relevance=float(scores.get("answer_relevance", 0.0) or 0.0),
                context_relevance=float(scores.get("context_relevance", 0.0) or 0.0),
                skipped=bool(scores.get("skipped", False)),
                skip_reason=scores.get("skip_reason"),
                error=scores.get("error"),
                contexts_json=contexts_json,
            )

            for context in contexts or []:
                session.run(
                    """
                    MATCH (i:Insight)
                    WHERE i.content = $content OR $content CONTAINS i.content
                    SET i.avg_faithfulness = CASE
                            WHEN i.avg_faithfulness IS NULL THEN $faithfulness
                            ELSE (i.avg_faithfulness + $faithfulness) / 2.0
                        END,
                        i.avg_answer_relevance = CASE
                            WHEN i.avg_answer_relevance IS NULL THEN $answer_relevance
                            ELSE (i.avg_answer_relevance + $answer_relevance) / 2.0
                        END,
                        i.avg_context_relevance = CASE
                            WHEN i.avg_context_relevance IS NULL THEN $context_relevance
                            ELSE (i.avg_context_relevance + $context_relevance) / 2.0
                        END,
                        i.confidence = CASE
                            WHEN $context_relevance >= 0.5
                            THEN CASE
                                WHEN coalesce(i.confidence, 0.5) + 0.05 > 1.0 THEN 1.0
                                ELSE coalesce(i.confidence, 0.5) + 0.05
                            END
                            ELSE CASE
                                WHEN coalesce(i.confidence, 0.5) - 0.05 < 0.0 THEN 0.0
                                ELSE coalesce(i.confidence, 0.5) - 0.05
                            END
                        END,
                        i.upvotes = coalesce(i.upvotes, 0) + CASE WHEN $context_relevance >= 0.5 THEN 1 ELSE 0 END,
                        i.downvotes = coalesce(i.downvotes, 0) + CASE WHEN $context_relevance < 0.5 THEN 1 ELSE 0 END
                    """,
                    content=context,
                    faithfulness=float(scores.get("faithfulness", 0.0) or 0.0),
                    answer_relevance=float(scores.get("answer_relevance", 0.0) or 0.0),
                    context_relevance=float(scores.get("context_relevance", 0.0) or 0.0),
                )

    def record_supervised_feedback(
        self,
        task_name,
        run_id,
        trace_id,
        expected_answer,
        actual_answer,
        is_correct,
        tool_name="general_tool",
    ):
        actual = str(actual_answer or "").strip()
        expected = str(expected_answer or "").strip()
        if is_correct:
            content = (
                "High-confidence success strategy: this run selected the ground-truth answer "
                f"'{expected}'. Reuse only the evidence-gathering strategy, not the answer itself; "
                "verify candidate genes with phenotype-specific biological and GWAS evidence before final selection."
            )
            insight_type = "success_strategy"
            confidence = 0.95
            operation = "UPVOTE"
        else:
            content = (
                "Failure pattern to avoid: this run selected "
                f"'{actual[:120]}' but the ground truth was '{expected}'. "
                "Do not over-weight broad literature co-occurrence or generic gene mentions. "
                "Re-run candidate-by-candidate verification and prioritize phenotype-specific GWAS, "
                "hematopoiesis/RBC biology, and direct causal evidence before choosing the final gene."
            )
            insight_type = "failure_pattern"
            confidence = 0.9
            operation = "ADD"
        self.upsert_insight(
            task_name=task_name,
            tool_name=tool_name,
            insight=content,
            metadata={
                "insight_type": insight_type,
                "confidence": confidence,
                "success": bool(is_correct),
                "operation": operation,
                "trace_id": trace_id,
                "run_id": run_id,
                "expected_answer": expected,
                "actual_answer": actual,
            },
        )
        with self.driver.session() as session:
            existing = session.run(
                """
                MATCH (r:Run {trace_id: $trace_id})
                RETURN r.conv_id AS conv_id
                LIMIT 1
                """,
                trace_id=trace_id,
            ).single()
            feedback_run_id = existing["conv_id"] if existing and existing["conv_id"] else run_id
            session.run(
                """
                MERGE (t:Task {name: $task})
                MERGE (r:Run {conv_id: $run_id})
                SET r.trace_id = $trace_id,
                    r.expected_answer = $expected,
                    r.actual_answer = $actual,
                    r.is_correct = $is_correct,
                    r.success = $is_correct,
                    r.updated_at = datetime()
                MERGE (t)-[:HAS_RUN]->(r)
                """,
                task=task_name,
                run_id=feedback_run_id,
                trace_id=trace_id,
                expected=expected,
                actual=actual,
                is_correct=bool(is_correct),
            )
            session.run(
                """
                MATCH (r:Run {trace_id: $trace_id})
                SET r.expected_answer = $expected,
                    r.actual_answer = $actual,
                    r.is_correct = $is_correct,
                    r.success = $is_correct,
                    r.supervised_feedback_at = datetime()
                WITH r
                OPTIONAL MATCH (r)-[:PRODUCED_EXPERIENCE]->(e:Experience)
                SET e.outcome = CASE WHEN $is_correct THEN 'success' ELSE 'failure' END,
                    e.score = CASE WHEN $is_correct THEN 1.0 ELSE 0.0 END,
                    e.supervised_corrected = true
                """,
                trace_id=trace_id,
                expected=expected,
                actual=actual,
                is_correct=bool(is_correct),
            )
            session.run(
                """
                MATCH (i:Insight)
                WHERE i.metadata_json CONTAINS $trace_id
                  AND coalesce(i.insight_type, '') <> 'failure_pattern'
                SET i.confidence = CASE
                        WHEN $is_correct AND coalesce(i.confidence, 0.5) + 0.25 > 1.0 THEN 1.0
                        WHEN $is_correct THEN coalesce(i.confidence, 0.5) + 0.25
                        ELSE 0.2
                    END,
                    i.status = CASE WHEN $is_correct THEN 'active' ELSE 'inactive' END,
                    i.supervised_corrected = true,
                    i.downvotes = coalesce(i.downvotes, 0) + CASE WHEN $is_correct THEN 0 ELSE 1 END,
                    i.upvotes = coalesce(i.upvotes, 0) + CASE WHEN $is_correct THEN 1 ELSE 0 END
                """,
                trace_id=trace_id or "",
                is_correct=bool(is_correct),
            )
            session.run(
                """
                MATCH (r:Run {trace_id: $trace_id})
                MATCH (r)-[:SELECTED_TOOL|SELECTED_DATA|SELECTED_LIBRARY]->(res)
                WHERE res:Tool OR res:DataResource OR res:Library
                SET res.success_count = coalesce(res.success_count, 0) + CASE WHEN $is_correct THEN 1 ELSE 0 END,
                    res.failure_count = coalesce(res.failure_count, 0) + CASE WHEN $is_correct THEN 0 ELSE 1 END,
                    res.confidence = CASE
                        WHEN $is_correct AND coalesce(res.confidence, 0.5) + 0.1 > 1.0 THEN 1.0
                        WHEN $is_correct THEN coalesce(res.confidence, 0.5) + 0.1
                        WHEN coalesce(res.confidence, 0.5) - 0.15 < 0.0 THEN 0.0
                        ELSE coalesce(res.confidence, 0.5) - 0.15
                    END,
                    res.updated_at = datetime()
                """,
                trace_id=trace_id,
                is_correct=bool(is_correct),
            )
        
    def update_insight_feedback(self, task_name, tool_name, is_correct, agent_output):
        vote_change = 1 if is_correct else -1
        query = """
        // 해당 도구의 인사이트를 찾고 점수를 업데이트 (강화학습적 요소)
        MATCH (tl:Tool {name: $tool})-[:HAS_GLOBAL_INSIGHT]->(gi:GlobalInsight)
        SET gi.upvotes = coalesce(gi.upvotes, 0) + $vote_change
        WITH tl
        
        // 오답일 경우, 추후 분석을 위해 실패 궤적 저장 (Reflexion 트리거용)
        FOREACH(ignoreMe IN CASE WHEN $is_correct = false THEN [1] ELSE [] END |
            MERGE (t:Task {name: $task})
            MERGE (f:FailedTrajectory {output: $output})
            MERGE (t)-[:FAILED_WITH]->(f)-[:USED_TOOL]->(tl)
        )
        """
        with self.driver.session() as session:
            session.run(query, task=task_name, tool=tool_name, is_correct=is_correct, 
                        vote_change=vote_change, output=agent_output)
