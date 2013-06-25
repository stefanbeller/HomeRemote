import RPi.GPIO as GPIO
import tornado.websocket
import tornado.ioloop
import tornado.web
import tornado.template
import gui
import signal
import thread
from remotehome import clients, event, events, inputs, outputs, gpio, security, cur, con
import sys

security = security()

for i in sys.argv:
    if not i == "-nogui":
        gui.start()
       
if __name__ == "__main__":
    # Get pins and set them up
    cur.execute("SELECT name, pin FROM lights")
    data = cur.fetchall()
    for i in data:
        outputs[str(i['pin'])] = gpio(i['pin'], i['name'], 'out')
        
    # Get inputs and set them up
    cur.execute("SELECT name, pin FROM inputs")
    data = cur.fetchall()
    for i in data:
        inputs[int(i['pin'])] = gpio(i['pin'], i['name'], 'in')
    
    # Get events and set them up
    cur.execute("SELECT id FROM events1")
    eventsdata = cur.fetchall()
    for i in eventsdata:
        events[int(i['id'])] = event(int(i['id']), cur)

class WebSocket(tornado.websocket.WebSocketHandler):
    def open(self):
        clients.append(self)
        gui.console("Websocket Opened")

    def on_message(self, message):
        gui.console(message)
        args = message.split(":")
        if args[0] == "newoutput":
            if args[1] in outputs:
                self.write_message("error:GPIO already setup on pin " + args[1])
            else:
                outputs[args[1]] = gpio(args[1], args[2], 'out')
                cur.execute("""INSERT INTO lights VALUES (NULL,%s,%s)""",(args[2],args[1]))
                con.commit()
                for i in clients:
                    i.write_message("addlight:"+args[1]+":"+args[2])
                
        elif args[0] == "getoutputson":
            total = 0
            on = 0
            for i in outputs:
                if outputs[i].get_state() == 1:
                    on = on + 1;
                else:
                    total = total + 1;
            self.write_message("lightoverview:"+str(on)+":"+str(total));
        
        elif args[0] == "setoutputstate":
            if args[1] in outputs:
                res = outputs[args[1]].set_state(args[2])
                if res is True:
                    self.write_message("ok:")
                else:
                    self.write_message("error:" + res)
            else:
                self.write_message("error:Output does not exist on pin " + args[1])
                
        elif args[0] == "togglepin":
            if args[1] in outputs:
                res = outputs[args[1]].toggle(clients)
            else:
                self.write_message("error:Output does not exist on pin " + args[1])
                
        elif args[0] == "declarepins":
            for i in outputs:
                if outputs[i].get_state() == 1:
                    self.write_message("pinchange:"+str(outputs[i].pin)+":on")
                elif outputs[i].get_state() == 0:
                    self.write_message("pinchange:"+str(outputs[i].pin)+":off")
                   
        elif args[0] == "declareevents":
            for i in events:
                if events[i].event_process != None:
                    self.write_message("eventchange:"+str(events[i].id)+":on")
                else:
                    self.write_message("eventchange:"+str(events[i].id)+":off")
                    
        elif args[0] == "toggleevent":
            args[1] = int(args[1])
            if events[args[1]].event_process != None:
                events[args[1]].stop_event()
                for i in clients:
                    i.write_message("eventchange:"+str(args[1])+":off")   
            else:
                events[args[1]].start_event()
                for i in clients:
                    i.write_message("eventchange:"+str(args[1])+":on")

        elif args[0] == "deletelight":
            cur.execute("""DELETE FROM lights WHERE pin = '%s'""",(int(args[1])))
            con.commit()
            del outputs[args[1]]
            for i in clients:
                i.write_message("deletelight:"+args[1])
            gui.remove_output(args[1])
                
        elif args[0] == "newevent":
            args[5] = args[5].replace("-", ":")
            cur.execute("""INSERT INTO events1 VALUES (NULL,%s,%s,%s,%s,%s,%s)""",(args[1],args[2], args[3], args[4], args[5], args[6]))
            con.commit()
            cur.execute("SELECT `id`, `trigger` FROM events1 WHERE name = '"+args[1]+"'")
            eventdat = cur.fetchone()
            events[eventdat['id']] = event(eventdat['id'], cur)
            
        elif args[0] == "deleteevent":
            events[int(args[1])].stop_event()
            del events[int(args[1])]
            cur.execute("""DELETE FROM events1 WHERE id = '%s'""",(int(args[1])))
            con.commit()
            for i in clients:
                i.write_message("deleteevent:"+args[1])
            gui.remove_event(args[1])
                
        elif args[0] == "newinput":
            if args[1] in inputs:
                self.write_message("error:Input already exists on pin "+args[2])
            else:
                cur.execute("""INSERT INTO inputs VALUES (NULL,%s,%s,%s)""",(args[1],args[2],args[3]))
                con.commit()
                inputs[args[2]] = gpio(args[2], args[1], "in")
                #events[int(args[2])] = event(0, True, args[2])
                
        elif args[0] == "deleteinput":
            cur.execute("SELECT name FROM events1 WHERE `trigger` = "+args[1])
            if cur.fetchone():
                self.write_message("error:Please delete the associated event for this input first!")
            else:
                cur.execute("""DELETE FROM inputs WHERE pin = %s""",(int(args[1])))
                con.commit()
                inputs[int(args[1])].stop_input_idle()
                del inputs[int(args[1])]
                for i in clients:
                    i.write_message("deleteinput:"+args[1])
                gui.remove_input(args[1])
                
        elif args[0] == "securitystatus":
            if security.armed_status:
                self.write_message("securitystatus:armed:"+security.mode)
            else:
                self.write_message("securitystatus:disarmed")
                
        elif args[0] == "armalarm":
            thread.start_new_thread(security.arm_system, (args[1],))
            for i in clients:
                i.write_message("securitystatus:armed:"+args[1])
            
        elif args[0] == "disarmalarm":
            security.disarm_system()
            for i in clients:
                i.write_message("securitystatus:disarmed")
                    
    def on_close(self):
        gui.console("Websocket closed")
        clients.remove(self)

application = tornado.web.Application([(r"/", WebSocket),])
def signal_handler(signal, frame):
        gui.console('Closing server')
        for i in clients:
            i.close()
        for i in events:
            events[i].stop_event()
        for i in inputs:
            if inputs[i].idling:
                inputs[i].stop_input_idle()
        GPIO.cleanup()
        gui.end()
        sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)

if __name__ == "__main__":
    application.listen(9000, '0.0.0.0')
    gui.console("Server started. Waiting for clients")
    tornado.ioloop.IOLoop.instance().start()