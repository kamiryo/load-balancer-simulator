import asyncio
import sys
from playwright.async_api import async_playwright

async def run_tests():
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        def on_console(msg):
            if msg.type == 'error':
                errors.append(f"Console error: {msg.text}")
        page.on('console', on_console)
        page.on('pageerror', lambda err: errors.append(f"Page error: {err}"))

        print(">>> Navigating to http://127.0.0.1:8090/index.html...")
        await page.goto("http://127.0.0.1:8090/index.html")
        await page.wait_for_timeout(1000)

        # 1. Check title & canvas
        title = await page.title()
        print(f"Page Title: {title}")
        canvas = await page.query_selector("#packetCanvas")
        assert canvas is not None, "packetCanvas not found"

        # 2. Check that JSESSIONID contains the AP name and clients have targetAp
        print("\n--- Testing AP-based JSESSIONID Generation & Sticky AP Mapping ---")
        clients_data = await page.evaluate("""() => {
            return State.clients.slice(0, State.clientCount).map(c => ({
                id: c.id,
                name: c.name,
                sessionId: c.sessionId,
                targetWeb: c.targetWeb,
                targetAp: c.targetAp,
                system: c.system
            }));
        }""")
        for c in clients_data:
            print(f"  {c['name']}: JSESSIONID={c['sessionId']} -> Sticky: {c['targetWeb']} -> {c['targetAp']}")
            assert c['targetAp'] is not None, f"{c['name']} must have targetAp"
            ap_tag = c['targetAp'].replace('-', '')
            assert ap_tag in c['sessionId'], f"JSESSIONID {c['sessionId']} must contain AP identifier {ap_tag}"

        # 3. Verify packets are strictly routed to the assigned targetAp
        print("\n--- Verifying Web-AP Routing sends packets to the AP that generated the JSESSIONID ---")
        await page.wait_for_timeout(1500)
        routed_packets = await page.evaluate("""() => {
            return packets.filter(p => p.apNode && p.clientRef && p.clientRef.sessionId).map(p => ({
                clientName: p.clientRef.name,
                expectedAp: p.clientRef.targetAp,
                actualAp: p.apNode.name
            }));
        }""")
        print(f"  Checked {len(routed_packets)} in-flight packets with active sessions:")
        assert len(routed_packets) > 0, "There should be in-flight packets routed to AP"
        for p_info in routed_packets[:8]:
            print(f"    {p_info['clientName']}: expected {p_info['expectedAp']} == actual {p_info['actualAp']}")
            assert p_info['expectedAp'] == p_info['actualAp'], f"Packet from {p_info['clientName']} must route to {p_info['expectedAp']}, but went to {p_info['actualAp']}"

        # 3-B. Verify that JSESSIONID remains completely unchanged across multiple normal requests
        print("\n--- Verifying JSESSIONID is NOT updated per request (kept static until error) ---")
        initial_sessions = await page.evaluate("""() => {
            return State.clients.slice(0, State.clientCount).map(c => ({ id: c.id, sessionId: c.sessionId }));
        }""")
        await page.wait_for_timeout(2500) # Wait 2.5s while packets keep flowing
        current_sessions = await page.evaluate("""() => {
            return State.clients.slice(0, State.clientCount).map(c => ({ id: c.id, sessionId: c.sessionId }));
        }""")
        for init_s in initial_sessions:
            cur_s = next(c for c in current_sessions if c['id'] == init_s['id'])
            assert init_s['sessionId'] == cur_s['sessionId'], f"Client {init_s['id']} JSESSIONID changed from {init_s['sessionId']} to {cur_s['sessionId']} without error!"
        print("  Confirmed: JSESSIONID remained 100% unchanged during continuous traffic without error.")

        # 4. Test Single AP Failover (drop AP-02, client receives error -> new JSESSIONID on return -> stays static)
        print("\n--- Testing Single AP Failure & Re-assignment by New AP ---")
        # Find a client bound to AP-02
        c_ap02 = next((c for c in clients_data if c['targetAp'] == 'AP-02'), None)
        if c_ap02:
            print(f"  Found client bound to AP-02: {c_ap02['name']} ({c_ap02['sessionId']})")
            # Drop AP-02
            await page.click("#card-ap_02")
            await page.wait_for_timeout(400)
            status_ap02 = await page.locator("#lb-badge-ap_02").inner_text()
            print(f"  AP-02 status after drop: {status_ap02}")
            assert "down/-" in status_ap02

            # Wait for failover to another AP (e.g. AP-04 or AP-06) and new JSESSIONID re-assignment
            reassigned = False
            new_session_id = None
            for sec in range(1, 16):
                await page.wait_for_timeout(800)
                cur_client = await page.evaluate(f"""() => {{
                    return State.clients.find(c => c.id === {c_ap02['id']});
                }}""")
                if cur_client['targetAp'] != 'AP-02' and cur_client['sessionId'] is not None and 'AP02' not in cur_client['sessionId']:
                    new_session_id = cur_client['sessionId']
                    print(f"  Sec {sec}: {c_ap02['name']} successfully reassigned to new AP {cur_client['targetAp']} with new JSESSIONID: {new_session_id}!")
                    reassigned = True
                    break
            assert reassigned == True, f"{c_ap02['name']} should be re-assigned to another active AP after AP-02 died"

            # Verify that AFTER failover and re-assignment, JSESSIONID STAYS STATIC and is NOT updated per request!
            print("  Verifying new JSESSIONID stays static after failover (not regenerated per request)...")
            await page.wait_for_timeout(2500)
            after_client = await page.evaluate(f"""() => {{
                return State.clients.find(c => c.id === {c_ap02['id']});
            }}""")
            assert after_client['sessionId'] == new_session_id, f"Failover JSESSIONID should remain static, but changed from {new_session_id} to {after_client['sessionId']}"
            print(f"  Confirmed: New JSESSIONID ({new_session_id}) remained static across multiple post-failover requests!")

            # Recover AP-02
            await page.click("#card-ap_02")
            await page.wait_for_timeout(500)

        # 5. Check Sessions Tab with AP Column
        print("\n--- Testing Sessions Tab UI with AP Column ---")
        await page.click("#rtab-btn-sessions")
        await page.wait_for_timeout(500)
        table_text = await page.locator("#right-tab-content").inner_text()
        print(f"  Session tab preview: {table_text[:140].strip()}...")
        assert "AP (Sticky)" in table_text
        assert "AP-0" in table_text
        # Switch back to jobs
        await page.click("#rtab-btn-jobs")
        await page.wait_for_timeout(300)

        # 5-B. Test Graceful Drain during "WEB-A系切離-waitあり":
        # Existing A-system clients MUST stay on System A during wait time (NOT routed to System B)!
        print("\n--- Testing Graceful Drain (WEB-A系切離-waitあり) ---")
        btn_cut_a_wait = await page.wait_for_selector('button:has-text("WEB-A系切離-waitあり")', timeout=3000)
        await btn_cut_a_wait.click()
        await page.wait_for_timeout(500)

        # Confirm Web-02 has drainTimer > 0
        drain_val = await page.evaluate("() => State.nodes.web_02.drainTimer")
        print(f"  Web-02 drainTimer: {drain_val}s")
        assert drain_val > 0, "Web-02 should have active drain timer"

        # Check in-flight packets during drain: clients bound to Web-A must STILL route to Web-A!
        await page.wait_for_timeout(1000)
        drain_packets = await page.evaluate("""() => {
            return packets.filter(p => !p.isReturning && p.clientRef && (p.clientRef.targetWeb === 'Web-02' || p.clientRef.targetWeb === 'Web-04') && p.clientRef.sessionId).map(p => ({
                clientName: p.clientRef.name,
                targetWeb: p.clientRef.targetWeb,
                routedSys: p.system,
                webName: p.webNode ? p.webNode.name : null
            }));
        }""")
        print(f"  In-flight forward packets for Web-A clients during wait period: {len(drain_packets)}")
        assert len(drain_packets) > 0, "There should be active in-flight packets for Web-A clients"
        for dp in drain_packets[:8]:
            print(f"    {dp['clientName']} (Target: {dp['targetWeb']}) -> Routed to: {dp['routedSys']} ({dp['webName']})")
            assert dp['routedSys'] == 'A', f"During wait period, client bound to {dp['targetWeb']} must stay on System A, but routed to {dp['routedSys']}"
        print("  Confirmed: During wait period, existing sessions stay on their system and do NOT jump to the other system.")

        # Now recover Web-A
        btn_in_a = await page.wait_for_selector('button:has-text("WEB-A系組込")', timeout=3000)
        await btn_in_a.click()
        await page.wait_for_timeout(500)

        # 6. Test 9 Operation Jobs
        print("\n--- Testing 9 Operation Jobs ---")
        jobs_to_test = [
            ("WEB-A系切離-waitあり", "#lb-badge-web_02", "under-maintenance", "#lb-badge-web_04", "under-maintenance"),
            ("WEB-A系組込", "#lb-badge-web_02", "up/active", "#lb-badge-web_04", "up/active"),
            ("WEB-A系切離-waitなし", "#lb-badge-web_02", "under-maintenance", "#lb-badge-web_04", "under-maintenance"),
            ("WEB-A系組込", "#lb-badge-web_02", "up/active", "#lb-badge-web_04", "up/active"),
            ("WEB-B系切離-waitあり", "#lb-badge-web_01", "under-maintenance", "#lb-badge-web_03", "under-maintenance"),
            ("WEB-B系組込", "#lb-badge-web_01", "up/active", "#lb-badge-web_03", "up/active"),
            ("WEB-B系切離-waitなし", "#lb-badge-web_01", "under-maintenance", "#lb-badge-web_03", "under-maintenance"),
            ("WEB-B系組込", "#lb-badge-web_01", "up/active", "#lb-badge-web_03", "up/active"),
            ("WEB全台切離-waitあり", "#lb-badge-web_01", "under-maintenance", "#lb-badge-web_02", "under-maintenance"),
            ("WEB全台組込", "#lb-badge-web_01", "up/active", "#lb-badge-web_02", "up/active"),
            ("WEB全台切離-waitなし", "#lb-badge-web_01", "under-maintenance", "#lb-badge-web_02", "under-maintenance"),
            ("WEB全台組込", "#lb-badge-web_01", "up/active", "#lb-badge-web_02", "up/active"),
        ]

        for job_name, target1, expected1, target2, expected2 in jobs_to_test:
            btn = await page.wait_for_selector(f'button:has-text("{job_name}")', timeout=3000)
            await btn.click()
            await page.wait_for_timeout(300)
            val1 = await page.locator(target1).inner_text()
            val2 = await page.locator(target2).inner_text()
            assert expected1 in val1, f"Expected {expected1} in {target1}, got {val1}"
            assert expected2 in val2, f"Expected {expected2} in {target2}, got {val2}"
        print("  All 9 operation jobs verified.")

        # 6-B. Test In-flight Response Guarantee on 3 "waitなし" Cut Jobs:
        # (WEB-A系切離-waitなし, WEB-B系切離-waitなし, WEB全台切離-waitなし)
        # Even on immediate cut, in-flight packets that already passed External LB MUST complete and return green (200 OK)!
        print("\n--- Testing In-flight Response Guarantee on 3 'waitなし' Cut Jobs ---")
        nowait_scenarios = [
            ("WEB-A系切離-waitなし", "A", "WEB-A系組込"),
            ("WEB-B系切離-waitなし", "B", "WEB-B系組込"),
            ("WEB全台切離-waitなし", "ALL", "WEB全台組込")
        ]
        for cut_job, target_sys, rec_job in nowait_scenarios:
            btn_cut = await page.wait_for_selector(f'button:has-text("{cut_job}")', timeout=3000)
            await btn_cut.click()
            has_green = False
            for _ in range(15):
                await page.wait_for_timeout(200)
                sys_filter = '(p.system === "A" || p.system === "B")' if target_sys == 'ALL' else f'p.system === "{target_sys}"'
                ret_cnt = await page.evaluate(f"""() => {{
                    return packets.filter(p => {sys_filter} && p.isReturning && !p.isError && p.errorCode === 200).length;
                }}""")
                if ret_cnt > 0:
                    has_green = True
                    print(f"  Confirmed [{cut_job}]: In-flight AP/DB packets completed and returned green (200 OK)! count={ret_cnt}")
                    break
            assert has_green, f"[{cut_job}] In-flight packets should complete normally and return green!"
            btn_rec = await page.wait_for_selector(f'button:has-text("{rec_job}")', timeout=3000)
            await btn_rec.click()
            await page.wait_for_timeout(600)
        print("  All 3 'waitなし' in-flight response guarantee tests verified.")

        # 7. Test SORRY Server Response Flow (Request: Blue -> Response: Green)
        print("\n--- Testing SORRY Traffic Flow (Request -> Response Green Returning) ---")
        btn_sorry_fail = await page.wait_for_selector('button:has-text("WEB全台 down/-")', timeout=3000)
        await btn_sorry_fail.click()
        await page.wait_for_timeout(400)
        sorry_status = await page.locator("#lb-badge-web_sorry").inner_text()
        print(f"  SORRY status: {sorry_status}")
        assert "up/active" in sorry_status

        # Wait for SORRY returning packets (green) to exist
        found_sorry_returning = False
        for _ in range(15):
            await page.wait_for_timeout(300)
            ret_count = await page.evaluate("() => packets.filter(p => p.isSorry && p.isReturning).length")
            if ret_count > 0:
                print(f"  Confirmed: SORRY returning response packets found! (count = {ret_count})")
                found_sorry_returning = True
                break
        assert found_sorry_returning == True, "SORRY server must return green response packets back to clients"

        # Recover WEB
        btn_web_rec = await page.wait_for_selector('button:has-text("WEB全台 復旧")', timeout=3000)
        await btn_web_rec.click()
        await page.wait_for_timeout(400)
        sorry_after = await page.locator("#lb-badge-web_sorry").inner_text()
        assert "up/standby" in sorry_after
        print("  SORRY returned to up/standby successfully.")

        if errors:
            print("\n[ERROR] Errors encountered:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print("\n==========================================")
            print("[SUCCESS] ALL AP STICKY ROUTING & JOB TESTS PASSED!")
            print("==========================================")

        await browser.close()

if __name__ == '__main__':
    asyncio.run(run_tests())
