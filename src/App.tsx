import React, { useCallback, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { motion, AnimatePresence } from 'motion/react';
import { UploadCloud, FileText, CheckCircle2, ChevronRight, Sparkles, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Toaster } from '@/components/ui/sonner';
import { toast } from 'sonner';

export default function App() {
  const [files, setFiles] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);

  const onDrop = useCallback((acceptedFiles: File[]) => {
    setFiles(acceptedFiles);
    if (acceptedFiles.length > 0) {
      toast.success(`${acceptedFiles.length} file(s) selected for processing.`);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'application/msword': ['.doc'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx']
    },
    maxFiles: 5
  } as any);

  const removeFile = (e: React.MouseEvent, fileToRemove: File) => {
    e.stopPropagation();
    setFiles(files.filter(f => f !== fileToRemove));
  };

  const handleUpload = () => {
    if (files.length === 0) return;
    setIsUploading(true);
    // Simulate upload
    setTimeout(() => {
      setIsUploading(false);
      toast.success('Files uploaded successfully. AI analysis started.');
      setFiles([]);
    }, 2000);
  };

  return (
    <div className="min-h-screen bg-black text-zinc-50 font-sans selection:bg-white/20 overflow-hidden">
      <Toaster theme="dark" position="top-center" />
      
      {/* Navbar */}
      <nav className="fixed top-0 w-full z-50 border-b border-white/5 bg-black/50 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-full bg-white flex items-center justify-center">
              <div className="w-2 h-2 rounded-full bg-black" />
            </div>
            <span className="font-medium tracking-tight text-lg">BPCommentary</span>
          </div>
          <div className="hidden md:flex items-center gap-8 text-sm font-medium text-zinc-400">
            <a href="#" className="hover:text-white transition-colors">Platform</a>
            <a href="#" className="hover:text-white transition-colors">Solutions</a>
            <a href="#" className="hover:text-white transition-colors">Enterprise</a>
            <a href="#" className="hover:text-white transition-colors">Pricing</a>
          </div>
          <div className="flex items-center gap-4">
            <Button variant="ghost" className="text-zinc-400 hover:text-white hover:bg-white/5 hidden md:flex">
              Sign In
            </Button>
            <Button className="bg-white text-black hover:bg-zinc-200 rounded-full px-6 font-medium">
              Get Started
            </Button>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <main className="pt-32 pb-24 px-6 relative">
        {/* Abstract Background Glow */}
        <div className="absolute top-1/4 left-1/2 -translate-x-1/2 w-[800px] h-[400px] bg-white/[0.03] blur-[120px] rounded-full pointer-events-none" />

        <div className="max-w-5xl mx-auto text-center relative z-10">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-white/10 bg-white/5 backdrop-blur-md text-xs font-medium text-zinc-300 mb-8">
              <Sparkles className="w-3.5 h-3.5" />
              <span>BPC Max 2.0 is now live</span>
            </div>
            <h1 className="text-5xl md:text-7xl lg:text-8xl font-medium tracking-tighter mb-6 leading-[1.1]">
              Your BP’s Last Stop <br className="hidden md:block" />
              <span className="text-transparent bg-clip-text bg-gradient-to-b from-white to-white/40">
                Before the Bin.
              </span>
            </h1>
            <p className="text-lg md:text-xl text-zinc-400 max-w-2xl mx-auto font-light tracking-wide mb-16">
              Upload your pitch deck. Our AI combines top-tier VC logic with metaphysical insights to reveal exactly why you aren't getting funded.
            </p>
          </motion.div>

          {/* Upload Area */}
          <motion.div
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className="max-w-3xl mx-auto"
          >
            <div
              {...getRootProps()}
              className={`relative group cursor-pointer overflow-hidden rounded-3xl border transition-all duration-500 bg-zinc-950/50 backdrop-blur-sm
                ${isDragActive ? 'border-white/40 bg-white/5' : 'border-white/10 hover:border-white/20 hover:bg-white/[0.02]'}
              `}
            >
              <input {...getInputProps()} />
              
              <div className="px-8 py-20 flex flex-col items-center justify-center text-center">
                <div className={`w-20 h-20 rounded-full flex items-center justify-center mb-6 transition-all duration-500
                  ${isDragActive ? 'bg-white text-black scale-110' : 'bg-zinc-900 text-zinc-400 group-hover:bg-zinc-800 group-hover:text-zinc-300'}
                `}>
                  <UploadCloud className="w-8 h-8" />
                </div>
                
                <h3 className="text-2xl font-medium tracking-tight mb-2">
                  {isDragActive ? 'Drop to analyze' : 'Upload documents'}
                </h3>
                <p className="text-zinc-500 text-sm max-w-sm mx-auto">
                  Drag and drop your PDF or Word files here, or click to browse. Maximum 5 files per analysis.
                </p>
              </div>
            </div>

            {/* File List */}
            <AnimatePresence>
              {files.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className="mt-6 text-left"
                >
                  <div className="space-y-3">
                    {files.map((file, idx) => (
                      <motion.div
                        key={`${file.name}-${idx}`}
                        initial={{ opacity: 0, x: -20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 20 }}
                        className="flex items-center justify-between p-4 rounded-2xl border border-white/10 bg-zinc-900/50 backdrop-blur-sm"
                      >
                        <div className="flex items-center gap-4">
                          <div className="w-10 h-10 rounded-full bg-zinc-800 flex items-center justify-center text-zinc-400">
                            <FileText className="w-5 h-5" />
                          </div>
                          <div>
                            <p className="text-sm font-medium truncate max-w-[200px] md:max-w-md">{file.name}</p>
                            <p className="text-xs text-zinc-500">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
                          </div>
                        </div>
                        <button
                          onClick={(e) => removeFile(e, file)}
                          className="p-2 text-zinc-500 hover:text-white transition-colors rounded-full hover:bg-zinc-800"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </motion.div>
                    ))}
                  </div>
                  
                  <div className="mt-8 flex justify-end">
                    <Button
                      onClick={handleUpload}
                      disabled={isUploading}
                      className="bg-white text-black hover:bg-zinc-200 rounded-full px-8 py-6 text-base font-medium transition-all"
                    >
                      {isUploading ? (
                        <span className="flex items-center gap-2">
                          <div className="w-4 h-4 border-2 border-black/20 border-t-black rounded-full animate-spin" />
                          Processing...
                        </span>
                      ) : (
                        <span className="flex items-center gap-2">
                          Start Analysis <ChevronRight className="w-4 h-4" />
                        </span>
                      )}
                    </Button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        </div>
      </main>

      {/* Features Grid */}
      <section className="py-24 border-t border-white/5 bg-zinc-950/50">
        <div className="max-w-7xl mx-auto px-6">
          <div className="grid md:grid-cols-3 gap-8">
            {[
              {
                title: "Global Compliance",
                desc: "Automated legal and tax compliance checks across major markets including US, EU, and APAC."
              },
              {
                title: "Entity Structuring",
                desc: "Optimal corporate structure recommendations based on your business model and capital flow."
              },
              {
                title: "Risk Mitigation",
                desc: "Identify potential operational and regulatory risks before they impact your expansion."
              }
            ].map((feature, idx) => (
              <div key={idx} className="p-8 rounded-3xl border border-white/5 bg-black/20 hover:bg-white/[0.02] transition-colors">
                <div className="w-12 h-12 rounded-full bg-zinc-900 border border-white/10 flex items-center justify-center mb-6">
                  <CheckCircle2 className="w-5 h-5 text-zinc-400" />
                </div>
                <h4 className="text-xl font-medium tracking-tight mb-3">{feature.title}</h4>
                <p className="text-zinc-500 text-sm leading-relaxed">{feature.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
