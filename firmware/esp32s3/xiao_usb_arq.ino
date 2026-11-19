// USB-only, explicit ARM, XMOS VAD+energy, 2s history, 15s segments/2s overlap.
#include <Arduino.h>
#include <AudioTools.h>
#include <Wire.h>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <atomic>
#if !ARDUINO_USB_MODE || !ARDUINO_USB_CDC_ON_BOOT
#error Hardware CDC and JTAG + USB CDC On Boot must be enabled
#endif
constexpr size_t N=1000, HISTORY=128, QSIZE=320;
constexpr uint32_t TARGET=240000;
struct Packet { uint32_t id, seq; uint16_t bytes, kind; uint8_t data[N]; };
struct __attribute__((packed)) Header {
  char magic[8]; uint32_t id, seq; uint16_t bytes, kind; uint32_t crc;
};
static_assert(sizeof(Header)==24, "header");
I2SStream i2s;
QueueHandle_t queue, commands;
StaticQueue_t queueObject;
uint8_t *history;
size_t histHead=0,histCount=0,used=0;
alignas(4) uint8_t input[1024];
uint8_t pcm[N],phase=0,votes[20]={},voteHead=0,voteCount=0,activeVotes=0;
int64_t sums[2]={};
bool armed=false,recording=false,manual=false,vadOk=false,speech=false;
uint32_t id=0,seq=0,frames=0,drops=0,dropStart=0,vadErrors=0;
uint64_t totalFrames=0,lastVoice=0;
std::atomic<uint32_t> txErrors{0};
uint32_t lastPoll=0,lastStatus=0;
uint32_t lastObservedTx=0;
float rmsSum=0,intervalRms=0;
uint32_t rmsCount=0;

uint32_t crcPart(uint32_t crc,const uint8_t* p,size_t n) {
  while(n--){crc^=*p++;for(int i=0;i<8;++i)crc=(crc>>1)^((crc&1)?0xedb88320UL:0);}
  return crc;
}

uint32_t lastAck=0;
bool haveAck=false,cancelPending=false;
char controlLine[24]={};size_t controlPos=0;
void readControl() {
  while(Serial.available()) {
    char c=Serial.read();
    if(c=='\n') {
      controlLine[controlPos]=0;uint8_t command=0;
      if(!strcmp(controlLine,"ARM"))command=1;
      if(!strcmp(controlLine,"STOP")){command=2;cancelPending=true;}
      if(!strcmp(controlLine,"TEST"))command=3;
      if(command)xQueueOverwrite(commands,&command);
      if(controlPos==12 && !strncmp(controlLine,"ACK ",4)) {
        char* end=nullptr;
        uint32_t value=strtoul(controlLine+4,&end,16);
        if(end && *end==0){lastAck=value;haveAck=true;}
      }
      controlPos=0;
    } else if(c!='\r') {
      if(controlPos<23)controlLine[controlPos++]=c;else controlPos=0;
    }
  }
}
void sendPacket(const Packet &p) {
  Header h={{'R','U','S','B','A','U','T','2'},p.id,p.seq,p.bytes,p.kind,0};
  h.crc=~crcPart(crcPart(0xffffffffUL,(uint8_t*)&h,20),p.data,p.bytes);
  uint8_t wire[24+N];memcpy(wire,&h,24);memcpy(wire+24,p.data,p.bytes);
  haveAck=false;cancelPending=false;
  // One in-flight frame. A lost ACK resends identical bytes; host deduplicates.
  for(unsigned attempt=0;attempt<4;++attempt) {
    size_t at=0;uint32_t progress=millis();
    while(at<24+p.bytes) {
      readControl();if(cancelPending)return;
      if(haveAck && lastAck==h.crc)return;
      size_t n=Serial.write(wire+at,24+p.bytes-at);
      if(n){at+=n;progress=millis();}
      else {if(millis()-progress>1000){++txErrors;return;}delay(1);}
    }
    uint32_t waiting=millis();
    while(millis()-waiting<250) {
      readControl();if(cancelPending)return;
      if(haveAck && lastAck==h.crc)return;
      delay(1);
    }
  }
  ++txErrors; // Main acquisition task disarms after exhausted retries.
}
void usbTask(void*) {
  Packet p;
  for(;;) {
    readControl();
    if(xQueueReceive(queue,&p,pdMS_TO_TICKS(2))==pdTRUE)sendPacket(p);
  }
}
void push(uint16_t kind,const void* data,uint16_t n) {
  Packet p={};p.id=id;p.seq=seq;p.kind=kind;p.bytes=n;
  if(n)memcpy(p.data,data,n);
  if(xQueueSend(queue,&p,0)!=pdTRUE)++drops;
}
void resetVotes(){memset(votes,0,20);voteHead=voteCount=activeVotes=0;}
bool readSpeech(bool &value) {
  Wire.beginTransmission(0x2c);Wire.write(20);Wire.write(18|0x80);Wire.write(5);
  if(Wire.endTransmission()!=0)return false;
  if(Wire.requestFrom((uint8_t)0x2c,(uint8_t)5)!=5)return false;
  uint8_t status=Wire.read(),data[4];for(auto &v:data)v=Wire.read();
  if(status!=0)return false;
  uint16_t flag=uint16_t(data[2])|(uint16_t(data[3])<<8);
  if(flag>1)return false; // Do not interpret an unrelated command as VAD.
  value=(flag==1);return true;
}
void beginSegment(size_t prefix) {
  id=esp_random();seq=frames=0;dropStart=drops;recording=true;
  prefix=min(prefix,histCount);
  uint32_t meta[6]={16000,2|(16UL<<16),TARGET,txErrors.load(),uint32_t(prefix*250),dropStart};
  uint8_t data[32];memcpy(data,meta,24);
  uint64_t start=totalFrames-prefix*250;memcpy(data+24,&start,8);
  push(1,data,32);
  size_t first=(histHead+HISTORY-prefix)%HISTORY;
  for(size_t i=0;i<prefix;++i){push(2,history+((first+i)%HISTORY)*N,N);++seq;frames+=250;}
}
void completedPacket() {
  memcpy(history+histHead*N,pcm,N);histHead=(histHead+1)%HISTORY;
  if(histCount<HISTORY)++histCount;
  totalFrames+=250;
  if(!recording)return;
  push(2,pcm,N);++seq;frames+=250;
  if(frames==TARGET){
    uint32_t report[3]={frames,drops-dropStart,txErrors.load()};push(3,report,12);
    recording=false;
    if(!manual && armed && vadOk && activeVotes>=12 && totalFrames-lastVoice<=32000)
      beginSegment(HISTORY);
    else resetVotes();
  }
}
void setup() {
  Serial.setTxBufferSize(8192);Serial.setRxBufferSize(256);Serial.setTxTimeoutMs(20);
  Serial.begin(921600);Serial.setDebugOutput(false);
  auto storage=(uint8_t*)heap_caps_malloc(QSIZE*sizeof(Packet),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
  history=(uint8_t*)heap_caps_malloc(HISTORY*N,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
  if(!storage||!history){Serial.println("FATAL PSRAM allocation failed");while(true)delay(1000);}
  queue=xQueueCreateStatic(QSIZE,sizeof(Packet),storage,&queueObject);
  commands=xQueueCreate(1,1);
  if(!queue||!commands)while(true)delay(1000);
  Wire.begin();Wire.setTimeOut(20);
  AudioInfo info(48000,2,32);auto cfg=i2s.defaultConfig(RX_MODE);cfg.copyFrom(info);
  cfg.pin_bck=8;cfg.pin_ws=7;cfg.pin_data=44;cfg.pin_data_rx=43;cfg.is_master=false;
  if(!i2s.begin(cfg)){Serial.println("FATAL I2S init failed");while(true)delay(1000);}
  xTaskCreatePinnedToCore(usbTask,"usbAudio",6144,nullptr,1,nullptr,0);
}
void loop() {
  size_t have=0;while(have<sizeof(input)){
    size_t n=i2s.readBytes(input+have,sizeof(input)-have);if(n)have+=n;else delay(1);
  }
  uint8_t command;
  uint32_t errors=txErrors.load();
  if(errors!=lastObservedTx && (armed||recording)){
    armed=recording=false;resetVotes();xQueueReset(queue);
  }
  lastObservedTx=errors;
  if(xQueueReceive(commands,&command,0)==pdTRUE){
    if(command==1){recording=false;armed=vadOk;manual=false;resetVotes();xQueueReset(queue);lastStatus=0;}
    if(command==2){armed=false;recording=false;manual=false;resetVotes();xQueueReset(queue);lastStatus=0;}
    if(command==3&&!recording){manual=true;armed=false;beginSegment(0);}
  }
  auto samples=(int32_t*)input;double energy=0;
  for(size_t f=0;f<128;++f){
    double v=double(samples[f*2])/2147483648.0;energy+=v*v;
    sums[0]+=samples[f*2];sums[1]+=samples[f*2+1];
    if(++phase!=3)continue;phase=0;
    for(int c=0;c<2;++c){int32_t v=sums[c]/3/65536;
      int16_t s=(int16_t)constrain(v,-32768,32767);memcpy(pcm+used,&s,2);used+=2;sums[c]=0;}
    if(used==N){completedPacket();used=0;}
  }
  rmsSum+=sqrt(energy/128);++rmsCount;uint32_t now=millis();
  if(now-lastPoll>=50){
    lastPoll=now;intervalRms=rmsSum/rmsCount;rmsSum=0;rmsCount=0;
    vadOk=readSpeech(speech);
    bool active=vadOk&&speech&&intervalRms>=0.002f;
    if(!vadOk){++vadErrors;resetVotes();}
    else {
      if(voteCount==20)activeVotes-=votes[voteHead];else ++voteCount;
      votes[voteHead]=active;activeVotes+=active;voteHead=(voteHead+1)%20;
      if(active)lastVoice=totalFrames;
    }
    if(armed&&!recording&&active&&voteCount==20&&activeVotes>=12&&histCount==HISTORY)
      beginSegment(HISTORY);
  }
  if(now-lastStatus>=1000){
    lastStatus=now;char text[480];
    int n=snprintf(text,sizeof(text),"{\"firmware\":\"usb_auto_v2_arq\",\"armed\":%s,\"vad_ok\":%s,\"speech\":%s,\"rms\":%.6f,\"votes\":%u,\"recording\":%s,\"vad_errors\":%lu,\"drops\":%lu,\"tx_errors\":%lu,\"samples\":%llu}",
      armed?"true":"false",vadOk?"true":"false",speech?"true":"false",intervalRms,
      activeVotes,recording?"true":"false",(unsigned long)vadErrors,(unsigned long)drops,
      (unsigned long)txErrors.load(),(unsigned long long)totalFrames);
    push(4,text,n);
  }
}
