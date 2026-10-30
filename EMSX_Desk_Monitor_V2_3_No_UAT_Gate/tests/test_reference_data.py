"""필드 ID 검증·분할 응답·캐시·선택 서비스 장애 격리를 검증한다."""

from __future__ import annotations

from types import SimpleNamespace
from datetime import date, datetime
from typing import Any
import unittest
from unittest.mock import Mock

from reference_data import FIELD_INFO_CID, FIELD_IDS, ReferenceDataFeed


class Node:
    def __init__(self, value: Any) -> None:
        self.value = value

    def hasElement(self, field: str) -> bool:
        return isinstance(self.value, dict) and field in self.value

    def getElement(self, field: str) -> Node:
        return Node(self.value[field])

    def getElementAsString(self, field: str) -> str:
        return str(self.value[field])

    def getValue(self) -> Any:
        return self.value

    def isNull(self) -> bool:
        return self.value is None

    def numValues(self) -> int:
        return len(self.value)

    def getValueAsElement(self, index: int) -> Node:
        return Node(self.value[index])


class Message(Node):
    def __init__(self, value: dict, cid: int, name: str = "모의 응답") -> None:
        super().__init__(value)
        self.cid = cid
        self.name = name

    def correlationIds(self) -> list:
        return [SimpleNamespace(value=lambda: self.cid)]

    def messageType(self) -> str:
        return self.name


class Request:
    def __init__(self, name: str) -> None:
        self.name = name
        self.fields: dict[str, list] = {}

    def getElement(self, field: str) -> Any:
        return SimpleNamespace(
            appendValue=lambda value: self.fields.setdefault(field, []).append(value)
        )


class TestReferenceData(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = [0.0]
        self.emitted = []
        self.warn = Mock()
        self.sent = []
        self.session = SimpleNamespace(
            openServiceAsync=Mock(return_value=True),
            getService=lambda service: SimpleNamespace(createRequest=Request),
            sendRequest=lambda request, correlationId: self.sent.append(
                (request, correlationId)
            ),
        )
        self.feed = ReferenceDataFeed(
            self.session,
            SimpleNamespace(CorrelationId=lambda cid: cid),
            lambda typ, **kw: self.emitted.append((typ, kw)),
            self.warn,
            now=lambda: self.clock[0],
        )

    def prepare(self) -> None:
        self.feed.queue_security("TEST JP Equity")
        self.feed.tick()
        self.feed.handle_service(
            Message({"serviceName": "//blp/apiflds"}, 70001, "ServiceOpened")
        )
        self.feed.handle_service(
            Message({"serviceName": "//blp/refdata"}, 70002, "ServiceOpened")
        )
        self.feed.tick()
        mapping = {
            "HS005": "VOLUME_AVG_5D",
            "HS007": "VOLUME_AVG_20D",
            "DS290": "COUNTRY_ISO",
            "DS213": "SECURITY_TYP",
            "DV018": "DVD_EX_DT",
        }
        self.feed.handle_response(
            Message(
                {
                    "fieldData": [
                        {
                            "id": field,
                            "fieldInfo": {
                                "mnemonic": mnemonic,
                                "description": "모의 필드",
                            },
                        }
                        for field, mnemonic in mapping.items()
                    ]
                },
                FIELD_INFO_CID,
            ),
            final=True,
        )
        self.feed.tick()

    def data(self, country: str = "JP") -> dict:
        return {
            "securityData": [
                {
                    "security": "TEST JP Equity",
                    "fieldData": {
                        "COUNTRY_ISO": country,
                        "SECURITY_TYP": "Common Stock",
                        "VOLUME_AVG_5D": 10000,
                        "VOLUME_AVG_20D": 20000,
                    },
                }
            ]
        }

    def test_field_ids_are_resolved_before_reference_request(self) -> None:
        self.prepare()
        request, cid = self.sent[0]
        self.assertEqual(request.name, "FieldInfoRequest")
        self.assertEqual(set(request.fields["id"]), set(FIELD_IDS))
        self.assertEqual(cid, FIELD_INFO_CID)
        request, cid = self.sent[1]
        self.assertEqual(request.name, "ReferenceDataRequest")
        self.assertEqual(request.fields["securities"], ["TEST JP Equity"])
        self.assertIn("VOLUME_AVG_20D", request.fields["fields"])
        self.assertIn("DVD_EX_DT", request.fields["fields"])
        self.assertGreaterEqual(cid, 1000000)

    def test_partial_responses_finish_after_all_messages(self) -> None:
        self.prepare()
        cid = self.feed.pending[0]
        self.feed.handle_response(Message(self.data(), cid), final=False)
        self.feed.end_event(False)
        self.assertFalse(self.emitted)
        self.feed.handle_response(Message(self.data("HK"), cid), final=False)
        self.assertFalse(self.emitted)
        self.feed.end_event(True)
        self.assertIsNone(self.feed.pending)
        result = self.emitted[-1][1]["fields"]
        self.assertEqual(result["iso_country"], "HK")
        self.assertEqual(result["avg_volume_20d"], 20000)
        self.assertEqual(result["sec_type"], "Common Stock")

    def test_dividend_native_dates_and_invalid_values_are_isolated(self) -> None:
        for value, expected in (
            (date(2026, 11, 2), "2026-11-02"),
            (datetime(2026, 11, 2, 0, 0), "2026-11-02"),
            ("20261102", "2026-11-02"),
            (None, None),
            ("2026-02-30", None),
            (True, None),
        ):
            with self.subTest(value=value):
                self.setUp()
                self.prepare()
                response = self.data("US")
                response["securityData"][0]["fieldData"]["DVD_EX_DT"] = value
                self.feed.handle_response(
                    Message(response, self.feed.pending[0]), final=True
                )
                fields = self.emitted[-1][1]["fields"]
                self.assertEqual(fields["dividend_ex_date"], expected)
                self.assertEqual(fields["iso_country"], "US")
                self.assertEqual(fields["avg_volume_20d"], 20000)

    def test_dividend_field_exception_clears_prior_partial_value(self) -> None:
        self.prepare()
        cid = self.feed.pending[0]
        response = self.data("US")
        response["securityData"][0]["fieldData"]["DVD_EX_DT"] = date(2026, 11, 2)
        self.feed.handle_response(Message(response, cid), final=False)
        response = {
            "securityData": [
                {
                    "security": "TEST JP Equity",
                    "fieldExceptions": [
                        {
                            "fieldId": "DVD_EX_DT",
                            "errorInfo": {"code": 1, "message": "권한 없음"},
                        }
                    ],
                }
            ]
        }
        with self.assertLogs("emsx", "WARNING"):
            self.feed.handle_response(Message(response, cid), final=True)
        fields = self.emitted[-1][1]["fields"]
        self.assertIsNone(fields["dividend_ex_date"])
        self.assertEqual(fields["iso_country"], "US")

    def test_dividend_mnemonic_is_verified_and_not_added_to_emsx_subscription(
        self,
    ) -> None:
        from bloomberg_feed import ORDER_FIELDS, ROUTE_FIELDS

        self.feed.handle_response(
            Message(
                {
                    "fieldData": [
                        {"id": "DV018", "fieldInfo": {"mnemonic": "DVD_PAY_DT"}}
                    ]
                },
                FIELD_INFO_CID,
            ),
            final=True,
        )
        self.assertNotIn("DV018", self.feed.mnemonics)
        self.assertTrue(self.warn.called)
        self.assertNotIn("DVD_EX_DT", ORDER_FIELDS)
        self.assertNotIn("DVD_EX_DT", ROUTE_FIELDS)

    def test_dividend_timeout_does_not_retain_previous_ex_date(self) -> None:
        self.prepare()
        self.clock[0] = 21
        self.feed.tick()
        self.assertIsNone(self.emitted[-1][1]["fields"]["dividend_ex_date"])

    def test_unverified_percentage_field_is_not_treated_as_volume(self) -> None:
        self.feed.handle_response(
            Message(
                {
                    "fieldData": [
                        {
                            "id": "HS007",
                            "fieldInfo": {"mnemonic": "UNKNOWN_QUANTITY_PERCENT"},
                        }
                    ]
                },
                FIELD_INFO_CID,
            ),
            final=True,
        )
        self.assertNotIn("HS007", self.feed.mnemonics)
        self.assertTrue(self.warn.called)

    def test_timeout_does_not_retain_old_metadata(self) -> None:
        self.prepare()
        self.clock[0] = 21
        self.feed.tick()
        self.assertIsNone(self.feed.pending)
        self.assertIsNone(self.emitted[-1][1]["fields"]["iso_country"])
        self.assertIsNone(self.emitted[-1][1]["fields"]["avg_volume_20d"])
        self.assertFalse(self.feed.queued)

    def test_service_failure_invalidates_data_and_can_reopen(self) -> None:
        self.prepare()
        self.assertTrue(
            self.feed.handle_service(
                Message({"serviceName": "//blp/refdata"}, 70002, "ServiceClosed")
            )
        )
        self.assertEqual(self.emitted[-1][0], "reference_unavailable")
        self.assertIsNone(self.feed.pending)
        self.assertFalse(
            self.feed.handle_service(
                Message({"serviceName": "//blp/emapisvc"}, 901, "ServiceClosed")
            )
        )
        self.clock[0] = 31
        self.feed.tick()
        self.assertGreaterEqual(self.session.openServiceAsync.call_count, 3)

    def test_cache_deduplicates_and_refreshes(self) -> None:
        self.prepare()
        cid = self.feed.pending[0]
        self.feed.handle_response(Message(self.data(), cid), final=True)
        self.feed.queue_security("test jp equity")
        self.feed.tick()
        self.assertEqual(len(self.sent), 2)
        self.clock[0] = 301
        self.feed.tick()
        self.assertEqual(len(self.sent), 3)

    def test_ack_correlation_id_is_never_consumed(self) -> None:
        self.assertFalse(
            self.feed.handle_response(Message({"STATUS": 0}, 121000), True)
        )
        self.assertFalse(self.emitted)

    def test_figi_request_and_canonical_response_use_original_identity(self) -> None:
        self.feed.queue_security("BBG000BLNNH6")
        self.feed.opened = {"//blp/apiflds", "//blp/refdata"}
        self.feed.started = True
        self.feed.info_sent = True
        self.feed.info_done = True
        self.feed.mnemonics = {"DS290": "COUNTRY_ISO"}
        self.feed.tick()
        self.assertEqual(self.sent[0][0].fields["securities"], ["/bbgid/BBG000BLNNH6"])
        cid = self.feed.pending[0]
        response = {
            "securityData": [
                {
                    "security": "CANONICAL HK Equity",
                    "sequenceNumber": 0,
                    "fieldData": {"COUNTRY_ISO": "HK"},
                }
            ]
        }
        self.feed.handle_response(Message(response, cid), final=True)
        self.assertEqual(self.emitted[-1][1]["security"], "BBG000BLNNH6")
        self.assertEqual(self.emitted[-1][1]["fields"]["iso_country"], "HK")

    def test_field_exception_clears_value_from_partial_response(self) -> None:
        self.prepare()
        cid = self.feed.pending[0]
        self.feed.handle_response(Message(self.data(), cid), final=False)
        self.feed.end_event(False)
        response = {
            "securityData": [
                {
                    "security": "TEST JP Equity",
                    "fieldExceptions": [
                        {
                            "fieldId": "VOLUME_AVG_20D",
                            "errorInfo": {"code": 1, "message": "권한 없음"},
                        }
                    ],
                }
            ]
        }
        with self.assertLogs("emsx", "WARNING"):
            self.feed.handle_response(Message(response, cid), final=True)
        self.assertIsNone(self.emitted[-1][1]["fields"]["avg_volume_20d"])
        self.assertEqual(self.emitted[-1][1]["fields"]["iso_country"], "JP")

    def test_global_response_error_discards_partial_metadata(self) -> None:
        self.prepare()
        cid = self.feed.pending[0]
        self.feed.handle_response(Message(self.data(), cid), final=False)
        self.feed.end_event(False)
        with self.assertLogs("emsx", "WARNING"):
            self.feed.handle_response(
                Message({"responseError": {"code": 11, "message": "거부"}}, cid),
                final=True,
            )
        self.assertIsNone(self.emitted[-1][1]["fields"]["iso_country"])
        self.assertIsNone(self.emitted[-1][1]["fields"]["avg_volume_5d"])

    def test_field_info_timeout_retries_have_distinct_ids_and_are_bounded(self) -> None:
        self.feed.opened = {"//blp/apiflds", "//blp/refdata"}
        self.feed.started = True
        for attempt in range(3):
            self.feed.tick()
            self.assertEqual(self.sent[-1][1], FIELD_INFO_CID + attempt)
            self.clock[0] += 31
            self.feed.tick()
        self.feed.tick()
        self.assertEqual(len(self.sent), 3)
        self.assertTrue(self.feed.info_done)
        self.assertFalse(self.feed.mnemonics)
